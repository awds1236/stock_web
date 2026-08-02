"""데이터 수집 파이프라인.

시장별 가용성이 다릅니다 -- 이것이 사용자 경험을 크게 좌우하므로 코드에
명시합니다:

    미국: yfinance 만 있으면 되고 **인증키가 필요 없습니다.** 앱을 켜자마자
          바로 수집·분석이 가능합니다.
    한국: KRX 인증키가 있어야 합니다. 키가 없으면 수집 자체가 불가능하므로,
          UI 는 이 상태를 '오류'가 아니라 '설정 필요'로 안내해야 합니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.providers.base import ProviderError
from app.store import Store, get_store

log = logging.getLogger(__name__)

# 기본 미국 유니버스. 인증키 없이 즉시 동작하는 경로를 만들기 위한 것이며,
# 실제 분석에서는 지수 구성종목으로 교체해야 합니다.
#
# 생존편향 경고: 이 목록은 '오늘 살아남은' 대형주입니다. 과거 시점에 존재했으나
# 상장폐지·피인수된 종목이 빠져 있으므로, 이 유니버스로 돌린 백테스트는
# 낙관적으로 편향됩니다. yfinance 가 상폐 종목을 제공하지 않는 문제와 겹칩니다.
DEFAULT_US_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "COST", "PEP", "KO", "ADBE", "CRM", "NFLX", "AMD",
    "INTC", "CSCO", "QCOM", "TXN", "AMAT", "MU", "LRCX", "KLAC",
    "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "SPGI",
    "LLY", "PFE", "TMO", "ABT", "DHR", "BMY", "AMGN", "GILD",
    "CAT", "DE", "BA", "HON", "GE", "LMT", "RTX", "UPS",
    "DIS", "CMCSA", "T", "VZ", "TMUS", "NKE", "SBUX", "MCD",
]


@dataclass
class IngestResult:
    market: str
    kind: str
    rows: int
    tickers: int
    start: date | None
    end: date | None
    warnings: list[str]


# 유니버스 상한. KRX 전 종목(2,700+)을 다 담으면 수집·예측이 느려지고, 무엇보다
# 횡단면 모형이 거래가 거의 없는 소형주 노이즈에 지배됩니다.
DEFAULT_UNIVERSE_LIMIT = 300

UNIVERSE_LIMIT_CAVEAT = (
    "유니버스를 **현재** 시가총액 상위 종목으로 제한했습니다. 과거 구간에도 이 "
    "목록을 적용하므로, 그동안 순위 밖으로 밀려났거나 상장폐지된 종목이 빠져 "
    "백테스트·예측 성과가 낙관적으로 편향됩니다(생존편향). 화면의 수치를 볼 때 "
    "이 점을 감안하십시오."
)


def limit_to_top_market_cap(
    panel: pd.DataFrame, limit: int = DEFAULT_UNIVERSE_LIMIT
) -> tuple[pd.DataFrame, int]:
    """가장 최근 일자의 시가총액 기준 상위 `limit` 종목만 남깁니다.

    왜 필요한가:
        KRX 전 종목은 2,700개가 넘습니다. 전부 담으면 수집이 느려질 뿐 아니라,
        횡단면 예측 모형이 하루 거래가 몇 백만원인 종목들의 노이즈에 지배되어
        분석 품질이 떨어집니다.

    정직한 한계 (호출자는 UNIVERSE_LIMIT_CAVEAT 를 함께 노출할 것):
        선정 기준이 **현재** 시가총액이므로, 과거 구간에 대해서는 미래 정보를
        쓴 셈입니다. 지금 대형주인 종목만 남으니 '그동안 살아남아 커진 기업'에
        표본이 쏠립니다. 엄밀히 하려면 시점별 상위 종목 목록이 필요하며, 그건
        무료 데이터로는 구하기 어렵습니다.

    Returns:
        (필터된 패널, 남은 종목 수)
    """
    if panel.empty or "market_cap" not in panel.columns:
        return panel, int(panel["ticker"].nunique()) if not panel.empty else 0

    latest = panel["date"].max()
    snapshot = panel[panel["date"] == latest].dropna(subset=["market_cap"])
    if snapshot.empty:
        # 시총 정보가 없으면 거래대금으로 대체합니다. 임의로 잘라내는 것보다
        # 규모 대용치를 쓰는 편이 낫습니다.
        snapshot = panel[panel["date"] == latest].dropna(subset=["value"])
        if snapshot.empty:
            return panel, int(panel["ticker"].nunique())
        keep = set(snapshot.nlargest(limit, "value")["ticker"])
    else:
        keep = set(snapshot.nlargest(limit, "market_cap")["ticker"])

    filtered = panel[panel["ticker"].isin(keep)]
    return filtered, len(keep)


def ingest_us_prices(
    tickers: list[str] | None = None,
    *,
    years: int = 10,
    since: date | None = None,
    with_classification: bool = True,
    store: Store | None = None,
) -> IngestResult:
    """미국 시세 수집 (yfinance, 인증키 불필요).

    섹터 정보도 함께 채웁니다 -- 섹터 단위 예측이 이 앱의 주요 기능이므로
    시세만 있고 섹터가 비면 절반만 쓸 수 있게 됩니다.

    Args:
        since: 이 날짜부터만 받아옵니다(증분 갱신용). 자동 갱신이 매번 10년치를
            내려받으면 요청량이 수백 배가 되고, Yahoo 가 조용히 차단합니다.
        with_classification: 섹터·업종 재조회 여부. 종목당 별도 요청이라 가장
            느린 구간인데, 분류는 거의 바뀌지 않으므로 증분 갱신에서는 끕니다.
            **기존 저장값이 있으면 그대로 유지합니다** -- 끈 채로 덮어쓰면 이미
            채워둔 섹터가 NULL 로 지워져 섹터 분석이 통째로 비게 됩니다.
    """
    from app.providers.yfinance_provider import (
        SURVIVORSHIP_WARNING,
        YFinancePriceProvider,
    )

    store = store or get_store()
    tickers = tickers or DEFAULT_US_UNIVERSE
    end = date.today()
    start = since or (end - timedelta(days=int(365.25 * years)))

    provider = YFinancePriceProvider()
    # yfinance 의 end 는 배타적이라 오늘 종가가 빠집니다. 하루 더 요청합니다.
    df = provider.fetch_history(tickers, start, end + timedelta(days=1))
    warnings = [SURVIVORSHIP_WARNING]

    if df.empty:
        return IngestResult("US", "prices", 0, 0, None, None,
                            [*warnings, "수집된 데이터가 없습니다."])

    classification = (
        _fetch_us_classification(tickers)
        if with_classification
        else _stored_us_classification(store, tickers)
    )
    df["sector"] = df["ticker"].map({t: c[0] for t, c in classification.items()})
    df["industry"] = df["ticker"].map({t: c[1] for t, c in classification.items()})
    if df["sector"].isna().any():
        warnings.append(
            "일부 종목의 섹터 정보를 가져오지 못했습니다. 해당 종목은 섹터 "
            "분석에서 제외됩니다."
        )

    # yfinance 는 시가총액 시계열을 주지 않습니다. 상장주식수를 알 수 없으므로
    # 시총은 비워둡니다 -- 종가로 임의 추정하면 규모 관련 특성이 조용히 틀립니다.
    rows = store.upsert_prices("US", df)
    store.log_ingest("US", "prices", end, rows)

    return IngestResult(
        market="US",
        kind="prices",
        rows=rows,
        tickers=int(df["ticker"].nunique()),
        start=df["date"].min(),
        end=df["date"].max(),
        warnings=warnings,
    )


def _stored_us_classification(
    store: Store, tickers: list[str]
) -> dict[str, tuple[str | None, str | None]]:
    """이미 저장된 섹터·업종을 그대로 재사용 (증분 갱신용).

    분류 조회는 종목당 별도 HTTP 요청이라 이 파이프라인에서 가장 느립니다.
    분류가 바뀌는 일은 드물기 때문에, 증분 갱신에서는 다시 묻지 않고 DB 에
    있는 값을 다시 씁니다.
    """
    universe = store.universe("US")
    if universe.empty:
        return {}
    wanted = set(tickers)
    return {
        str(r["ticker"]): (
            str(r["sector"]) if pd.notna(r["sector"]) else None,
            str(r["industry"]) if pd.notna(r.get("industry")) else None,
        )
        for _, r in universe.iterrows()
        if str(r["ticker"]) in wanted
    }


def _fetch_us_classification(tickers: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """yfinance 에서 종목별 (섹터, 세부업종) 조회.

    섹터(11개 대분류)만으로는 '반도체'와 '소프트웨어'가 전부 Technology 로
    뭉개지므로, 더 세밀한 industry(세부업종, 100여 개)를 함께 저장합니다.

    시점별 매핑이 아니라 **현재 시점 분류**입니다. 과거 구간에 현재 분류를
    적용하면 엄밀히는 미래 정보가 섞이지만, 섹터 재분류는 드물고 무료로 얻을
    수 있는 대안이 없어 이 한계를 안고 씁니다. 문서와 API 응답에 명시합니다.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    out: dict[str, tuple[str | None, str | None]] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            sector = info.get("sector")
            industry = info.get("industry")
            out[t] = (
                str(sector) if sector else None,
                str(industry) if industry else None,
            )
        except Exception as exc:  # noqa: BLE001 -- 개별 종목 실패로 전체를 막지 않습니다
            log.debug("분류 조회 실패 %s: %s", t, exc)
    return out


# KRX 정보데이터시스템의 업종분류현황 화면이 쓰는 내부 JSON (인증키 불필요).
# 공식 규격이 아니므로 실패는 경고로만 처리하고 시세 수집을 막지 않습니다.
_KRX_INDUSTRY_BLD = "dbms/MDC/STAT/standard/MDCSTAT03901"


def fetch_kr_classification(trade_date: date, *, tries: int = 5) -> pd.DataFrame:
    """KRX 업종분류현황: 종목별 한국 세부업종명 (예: '반도체', '은행').

    **여러 날짜를 시도합니다.** 이 화면은 휴장일에 빈 응답을 주는데, 한 날짜만
    묻고 포기하면 공휴일 하루 때문에 업종이 통째로 비게 됩니다 -- 그러면 섹터
    화면과 시장 화면의 '업종 지형'이 전부 사라집니다(실배포에서 확인).

    Returns:
        columns = [ticker, industry] -- KRX 업종명은 이미 한글이므로 번역이
        필요 없습니다. 모든 시도가 실패하면 빈 프레임.
    """
    import httpx

    from app.providers.krx_mdc import _HEADERS

    rows: list[dict] = []
    with httpx.Client(timeout=30.0, headers=_HEADERS) as client:
        day = trade_date
        for _ in range(tries):
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            for mkt in ("STK", "KSQ"):
                try:
                    resp = client.post(
                        "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                        data={
                            "bld": _KRX_INDUSTRY_BLD,
                            "mktId": mkt,
                            "trdDd": day.strftime("%Y%m%d"),
                            "money": "1",
                            "csvxls_isNo": "false",
                        },
                    )
                    resp.raise_for_status()
                    body = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    log.debug("업종분류 %s/%s 실패: %s", day, mkt, exc)
                    continue
                for value in body.values():
                    if isinstance(value, list):
                        rows.extend(value)
                        break
            if rows:
                break
            day -= timedelta(days=1)  # 휴장일이었을 수 있습니다

    if not rows:
        return pd.DataFrame(columns=["ticker", "industry"])
    raw = pd.DataFrame(rows)
    ticker_col = "ISU_SRT_CD" if "ISU_SRT_CD" in raw.columns else "ISU_CD"
    industry_col = next(
        (c for c in ("IDX_IND_NM", "SECT_TP_NM", "IND_NM") if c in raw.columns), None
    )
    if industry_col is None or ticker_col not in raw.columns:
        return pd.DataFrame(columns=["ticker", "industry"])
    return pd.DataFrame(
        {
            "ticker": raw[ticker_col].astype(str).str.strip(),
            "industry": raw[industry_col].astype(str).str.strip(),
        }
    ).drop_duplicates(subset=["ticker"])


# 한국 수집의 구조적 제약 -- 여기서부터 읽으십시오.
#
# KRX Open API 는 **하루치씩만** 줍니다. 미국(yfinance)이 10년치를 한 번에
# 주는 것과 정반대입니다. 10년 = 약 2,470 거래일 × 2개 시장 = 약 5,000회 요청.
#
# 이 차이를 무시하고 "30일치만" 받도록 두면 다음이 전부 빈칸이 됩니다:
#
#     60일 이동평균 · 52주 고점 · 상대강도 · 워크포워드 예측
#
# 실제로 배포된 사이트에서 한국 시장·예측 화면이 통째로 비어 있었고, 원인이
# 이것이었습니다. 그래서:
#
#   1. **이미 저장한 날은 다시 받지 않습니다.** 과거 확정 시세는 불변입니다.
#   2. **병렬로 받습니다.** 요청이 서로 독립이라 순차로 돌 이유가 없습니다.
#   3. **한 번에 받는 양에 상한을 둡니다.** 첫 실행이 몇 십 분 걸리면 CI 가
#      못 씁니다. 대신 실행할 때마다 과거로 조금씩 넓혀 갑니다.
KR_DEFAULT_YEARS = 5

# 한 번의 실행에서 새로 받아올 거래일 수. 요청으로는 그 2배입니다(KOSPI+KOSDAQ).
#
# 1,100 은 **실측으로 정한 값**입니다. 예측이 되는 최소 길이를 재 보면:
#
#     800거래일  -> 409 (구간을 만들 수 없음)
#     900거래일  -> 폴드 1
#   1,100거래일  -> 폴드 3
#
# 학습 504 + 검증 126 + 예측구간 21 만으로는 부족한 이유는 **특성 워밍업**
# 입니다. 12-1 모멘텀이 약 252거래일을 먹고 시작하므로 그만큼이 앞에서
# 통째로 버려집니다. 폴드 1개짜리 보정 지표는 근거가 너무 얇아, 캐시가 빈
# 첫 실행에서도 폴드 3개가 나오는 길이를 택했습니다.
KR_MAX_DAYS_PER_RUN = 1100
KR_FETCH_WORKERS = 6


def _kr_trading_days(start: date, end: date) -> list[date]:
    """주말을 뺀 날짜 목록 (최신 우선).

    공휴일은 여기서 걸러내지 않습니다 -- 한국 공휴일 달력을 코드에 넣으면
    매년 관리해야 하고 틀리면 조용히 구멍이 납니다. 휴장일은 KRX 가 빈 응답을
    주므로 그것으로 판별하며, 그 응답도 캐시되어 두 번 묻지 않습니다.
    """
    days: list[date] = []
    day = end
    while day >= start:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days


def _kr_stored_dates(store: Store) -> set[date]:
    with store.cursor() as con:
        rows = con.execute("SELECT DISTINCT date FROM prices WHERE market = 'KR'").fetchall()
    return {r[0] for r in rows}


def ingest_kr_prices(
    *,
    years: float = KR_DEFAULT_YEARS,
    days: int | None = None,
    max_days_per_run: int = KR_MAX_DAYS_PER_RUN,
    refetch_recent_days: int = 5,
    store: Store | None = None,
    limit: int = DEFAULT_UNIVERSE_LIMIT,
) -> IngestResult:
    """한국 시세 수집 (KRX Open API, **인증키 필요**).

    Args:
        years: 확보하려는 히스토리 길이. 예측(워크포워드)에는 최소 3년가량이
            필요합니다 -- 학습 504일 + 검증 126일 + 예측구간이 그만큼 됩니다.
        days: 달력일 기준 상한. 지정하면 `years` 를 무시합니다(이전 호출 방식
            호환 및 증분 갱신용).
        max_days_per_run: 한 번의 실행에서 새로 받아올 거래일 수 상한. 남은
            구간은 다음 실행이 이어받습니다.
        refetch_recent_days: 최근 며칠은 이미 있어도 다시 받습니다. 장 마감
            직후 데이터가 나중에 정정되는 경우가 있어서입니다.

    KRX 는 전 종목(2,700+)을 한 번에 주므로, 저장 **전에** 시총 상위 `limit`
    종목으로 줄입니다. 저장 후 걸러내면 DB 가 이미 비대해진 뒤라 의미가 없습니다.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.providers.base import CallBudgetExceeded
    from app.providers.krx_openapi import KrxOpenApiPriceProvider

    store = store or get_store()
    provider = KrxOpenApiPriceProvider()

    warnings: list[str] = []
    end = date.today()
    start = (
        end - timedelta(days=days)
        if days is not None
        else end - timedelta(days=int(365.25 * years))
    )

    wanted = _kr_trading_days(start, end)
    have = _kr_stored_dates(store)
    fresh_cutoff = end - timedelta(days=refetch_recent_days)
    todo = [d for d in wanted if d not in have or d >= fresh_cutoff]

    remaining_after = max(0, len(todo) - max_days_per_run)
    todo = todo[:max_days_per_run]

    frames: list[pd.DataFrame] = []
    budget_hit = False

    def fetch(day: date):
        try:
            return day, provider.fetch_daily_prices(day), None
        except CallBudgetExceeded as exc:
            return day, None, ("budget", str(exc))
        except ProviderError as exc:
            return day, None, ("provider", str(exc))

    if todo:
        with ThreadPoolExecutor(max_workers=min(KR_FETCH_WORKERS, len(todo))) as pool:
            for day, df, err in pool.map(fetch, todo):
                if err is not None:
                    kind, message = err
                    if kind == "budget":
                        budget_hit = True
                    elif len(warnings) < 5:
                        # 실패한 날짜를 전부 나열하면 경고가 수백 줄이 됩니다.
                        warnings.append(f"{day}: {message}")
                    continue
                if df is not None and not df.empty:
                    frames.append(df)

    if budget_hit:
        warnings.append(
            "KRX 일일 호출 한도에 걸려 수집을 중단했습니다. 받아둔 만큼은 "
            "저장되며, 나머지는 한도가 초기화된 뒤 다음 실행이 이어받습니다."
        )
    if remaining_after:
        warnings.append(
            f"히스토리 {remaining_after}거래일이 아직 남았습니다. KRX 는 하루치씩만 "
            f"제공하므로 한 번에 다 받지 않고 실행할 때마다 과거로 넓혀 갑니다. "
            f"그때까지 장기 지표(60일선·52주 고점)와 예측은 비어 있을 수 있습니다."
        )

    if not frames:
        return IngestResult("KR", "prices", 0, 0, None, None,
                            warnings or ["수집된 데이터가 없습니다."])

    all_df = pd.concat(frames, ignore_index=True)

    # 유니버스는 **한 번 정해지면 유지합니다.**
    #
    # 히스토리를 여러 번에 나눠 받으므로, 매 실행마다 그 배치의 최신일 시총으로
    # 상위 N을 다시 뽑으면 실행마다 종목 집합이 달라집니다. 그러면 종목별
    # 시계열에 구멍이 생기고(어떤 구간에만 존재), 그 구멍은 지표를 조용히
    # 틀리게 만듭니다. 이미 저장된 종목이 있으면 그 목록을 따릅니다.
    existing = set(store.universe("KR")["ticker"]) if not store.universe("KR").empty else set()
    total_tickers = int(all_df["ticker"].nunique())
    if existing:
        all_df = all_df[all_df["ticker"].isin(existing)]
        kept = int(all_df["ticker"].nunique())
    else:
        all_df, kept = limit_to_top_market_cap(all_df, limit)
    if kept < total_tickers:
        warnings.append(
            f"전체 {total_tickers:,}종목 중 시총 상위 {kept}종목만 유지했습니다. "
            + UNIVERSE_LIMIT_CAVEAT
        )
    if all_df.empty:
        return IngestResult("KR", "prices", 0, 0, None, None,
                            [*warnings, "이번 구간에 유니버스 종목의 시세가 없습니다."])

    # 세부업종(반도체·은행 등) 결합. KRX 업종명은 이미 한글입니다.
    # 실패해도 시세 수집을 막지 않습니다 -- 업종은 보조 정보입니다.
    try:
        cls = fetch_kr_classification(all_df["date"].max())
        if cls.empty:
            warnings.append(
                "업종분류를 가져오지 못했습니다(빈 응답). 섹터 화면이 비어 보일 수 "
                "있습니다. KRX 정보데이터시스템의 비공식 경로라 휴장일이나 규격 "
                "변경 시 실패합니다."
            )
        else:
            all_df = all_df.merge(cls, on="ticker", how="left")
            # KRX 대분류가 따로 없으므로 sector 도 업종명으로 채웁니다.
            all_df["sector"] = all_df.get("sector").fillna(all_df["industry"]) \
                if "sector" in all_df.columns else all_df["industry"]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"업종분류 조회 실패 (시세는 정상): {exc}")

    rows = store.upsert_prices("KR", all_df)
    store.log_ingest("KR", "prices", end, rows)
    return IngestResult(
        market="KR",
        kind="prices",
        rows=rows,
        tickers=int(all_df["ticker"].nunique()),
        start=all_df["date"].min(),
        end=all_df["date"].max(),
        warnings=warnings,
    )
