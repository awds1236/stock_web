"""DuckDB 저장소.

DuckDB 를 쓴 이유: 단일 파일이라 운영이 단순하고, pandas 왕복이 빠르며, 시계열
집계(윈도우 함수)가 강합니다. 별도 서버 프로세스가 필요 없습니다.

키 설계:
    (market, ticker, date) 를 기본키로 두어 재수집이 **멱등**하게 만듭니다.
    수집 잡은 실패·재시도가 잦으므로, 같은 날짜를 두 번 넣었을 때 행이 중복되면
    거래량·수급이 두 배로 계산되어 지표가 조용히 틀어집니다.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from app.config import settings


class StoreLocked(RuntimeError):
    """DB 파일이 다른 프로세스에 잠김.

    DuckDB 는 파일당 쓰기 프로세스를 하나만 허용합니다. API 서버와 수집
    스크립트를 동시에 돌리면 발생하며, 흔한 상황이므로 별도 예외로 구분해
    사용자에게 무엇을 해야 할지 알려줍니다.
    """


SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    market      VARCHAR NOT NULL,
    ticker      VARCHAR NOT NULL,
    date        DATE     NOT NULL,
    name        VARCHAR,
    board       VARCHAR,     -- KOSPI | KOSDAQ (한국). 미국은 NULL
    sector      VARCHAR,
    industry    VARCHAR,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    value       DOUBLE,
    market_cap  DOUBLE,
    shares      DOUBLE,
    is_delisted BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (market, ticker, date)
);

CREATE TABLE IF NOT EXISTS flows (
    market        VARCHAR NOT NULL,
    ticker        VARCHAR NOT NULL,
    date          DATE     NOT NULL,
    investor_type VARCHAR NOT NULL,
    buy_value     DOUBLE,
    sell_value    DOUBLE,
    net_value     DOUBLE,
    PRIMARY KEY (market, ticker, date, investor_type)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    market     VARCHAR NOT NULL,
    kind       VARCHAR NOT NULL,
    date       DATE     NOT NULL,
    rows       INTEGER,
    ingested_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (market, kind, date)
);
"""


class Store:
    """스레드 안전 DuckDB 래퍼.

    DuckDB 연결은 스레드 안전하지 않으므로, FastAPI 의 워커 스레드에서 동시에
    쓰이지 않도록 락으로 직렬화합니다. 단일 사용자 로컬 도구에서는 이 정도로
    충분하며, 성능 병목이 되면 연결 풀로 바꾸는 것이 다음 단계입니다.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.duckdb_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """지연 연결.

        생성 시점이 아니라 첫 사용 시점에 연결합니다. import 시점에 연결하면
        DB 를 전혀 쓰지 않는 요청(예: 설정 화면)까지 파일 락을 요구하게 됩니다.
        """
        if self._conn is not None:
            return self._conn
        try:
            conn = duckdb.connect(str(self.path))
        except duckdb.IOException as exc:
            # DuckDB 는 파일당 쓰기 프로세스를 하나만 허용합니다. 원본 예외를
            # 그대로 흘리면 사용자는 500 만 보고 원인을 알 수 없습니다.
            if "lock" in str(exc).lower():
                raise StoreLocked(
                    f"데이터베이스가 다른 프로세스에 잠겨 있습니다: {self.path}\n"
                    "DuckDB 는 한 번에 하나의 쓰기 프로세스만 허용합니다. "
                    "수집 스크립트나 다른 API 서버가 실행 중인지 확인하고 "
                    "종료한 뒤 다시 시도하십시오.\n"
                    f"원본 오류: {exc}"
                ) from exc
            raise
        conn.execute(SCHEMA)
        # 스키마 진화: 기존 DB 파일에는 새 컬럼이 없을 수 있습니다. CREATE TABLE
        # IF NOT EXISTS 는 기존 테이블을 바꾸지 않으므로, 여기서 명시적으로
        # 추가합니다. 이걸 빼먹으면 업그레이드한 사용자만 INSERT 가 깨집니다.
        conn.execute("ALTER TABLE prices ADD COLUMN IF NOT EXISTS industry VARCHAR")
        # 코스피/코스닥 구분. 기존 DB(캐시로 이어받는 CI 포함)에는 없으므로
        # 여기서 추가합니다 -- 빼먹으면 업그레이드한 쪽만 INSERT 가 깨집니다.
        conn.execute("ALTER TABLE prices ADD COLUMN IF NOT EXISTS board VARCHAR")
        self._conn = conn
        return conn

    @contextmanager
    def cursor(self):
        with self._lock:
            yield self._connect()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── 쓰기 ─────────────────────────────────────────────────────────────
    def upsert_prices(self, market: str, df: pd.DataFrame) -> int:
        """시세 저장 (멱등). 같은 (market, ticker, date) 는 덮어씁니다."""
        if df.empty:
            return 0
        frame = _prepare(df, market, PRICE_COLUMNS)
        with self.cursor() as con:
            con.register("incoming", frame)
            con.execute(
                """
                DELETE FROM prices
                USING incoming
                WHERE prices.market = incoming.market
                  AND prices.ticker = incoming.ticker
                  AND prices.date   = incoming.date
                """
            )
            con.execute(f"INSERT INTO prices ({', '.join(PRICE_COLUMNS)}) "
                        f"SELECT {', '.join(PRICE_COLUMNS)} FROM incoming")
            con.unregister("incoming")
        return len(frame)

    def upsert_flows(self, market: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        frame = _prepare(df, market, FLOW_COLUMNS)
        with self.cursor() as con:
            con.register("incoming", frame)
            con.execute(
                """
                DELETE FROM flows
                USING incoming
                WHERE flows.market = incoming.market
                  AND flows.ticker = incoming.ticker
                  AND flows.date   = incoming.date
                  AND flows.investor_type = incoming.investor_type
                """
            )
            con.execute(f"INSERT INTO flows ({', '.join(FLOW_COLUMNS)}) "
                        f"SELECT {', '.join(FLOW_COLUMNS)} FROM incoming")
            con.unregister("incoming")
        return len(frame)

    def log_ingest(self, market: str, kind: str, day: date, rows: int) -> None:
        with self.cursor() as con:
            con.execute(
                "INSERT OR REPLACE INTO ingest_log (market, kind, date, rows) "
                "VALUES (?, ?, ?, ?)",
                [market, kind, day, rows],
            )

    # ── 읽기 ─────────────────────────────────────────────────────────────
    def prices(
        self,
        market: str,
        tickers: list[str] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM prices WHERE market = ?"
        params: list = [market]
        if tickers:
            sql += f" AND ticker IN ({', '.join('?' * len(tickers))})"
            params += tickers
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY ticker, date"
        with self.cursor() as con:
            return con.execute(sql, params).fetchdf()

    def flows(
        self,
        market: str,
        tickers: list[str] | None = None,
        investor_type: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM flows WHERE market = ?"
        params: list = [market]
        if tickers:
            sql += f" AND ticker IN ({', '.join('?' * len(tickers))})"
            params += tickers
        if investor_type:
            sql += " AND investor_type = ?"
            params.append(investor_type)
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY ticker, date"
        with self.cursor() as con:
            return con.execute(sql, params).fetchdf()

    def universe(self, market: str) -> pd.DataFrame:
        """수집된 종목 목록과 데이터 보유 구간."""
        with self.cursor() as con:
            return con.execute(
                """
                SELECT ticker,
                       any_value(name)   AS name,
                       any_value(board)  AS board,
                       any_value(sector) AS sector,
                       any_value(industry) AS industry,
                       min(date)         AS first_date,
                       max(date)         AS last_date,
                       count(*)          AS n_days
                FROM prices WHERE market = ?
                GROUP BY ticker ORDER BY ticker
                """,
                [market],
            ).fetchdf()

    def stamp(self, market: str) -> tuple[str, int]:
        """이 시장 데이터의 지문 (마지막 일자, 행 수).

        캐시 무효화 기준으로 씁니다. 시간 기반 TTL 대신 이걸 쓰는 이유:
        자동 수집이 새 데이터를 넣었는데 옛 계산이 남아 있으면, 화면의 시세와
        분석이 서로 다른 날짜를 가리킵니다. 데이터가 바뀌었는지를 직접 보는
        편이 정확합니다.
        """
        with self.cursor() as con:
            row = con.execute(
                "SELECT max(date), count(*) FROM prices WHERE market = ?", [market]
            ).fetchone()
        return (str(row[0]) if row else "", int(row[1] or 0) if row else 0)

    def coverage(self) -> pd.DataFrame:
        """시장별 데이터 보유 현황. 프론트엔드가 '데이터 없음'을 안내할 때 씁니다."""
        with self.cursor() as con:
            return con.execute(
                """
                SELECT market,
                       count(DISTINCT ticker) AS n_tickers,
                       min(date) AS first_date,
                       max(date) AS last_date,
                       count(*)  AS n_rows
                FROM prices GROUP BY market ORDER BY market
                """
            ).fetchdf()


PRICE_COLUMNS = [
    "market", "ticker", "date", "name", "board", "sector", "industry", "open",
    "high", "low", "close", "volume", "value", "market_cap", "shares", "is_delisted",
]
FLOW_COLUMNS = [
    "market", "ticker", "date", "investor_type",
    "buy_value", "sell_value", "net_value",
]


def _prepare(df: pd.DataFrame, market: str, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    out["market"] = market
    for col in columns:
        if col not in out.columns:
            out[col] = None
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["ticker"] = out["ticker"].astype(str)
    if "is_delisted" in columns:
        out["is_delisted"] = out["is_delisted"].fillna(False).astype(bool)
    # 중복 키가 들어오면 마지막 값을 남깁니다. 그대로 두면 삽입은 되지만
    # 이후 집계가 조용히 두 배가 됩니다.
    key = [c for c in ("market", "ticker", "date", "investor_type") if c in columns]
    return out[columns].drop_duplicates(subset=key, keep="last")


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
