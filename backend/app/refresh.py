"""주가 자동 수집·갱신.

목적: 화면이 스스로 최신이 되게 하는 것. 사용자가 매번 "수집" 버튼을 눌러야
한다면 그건 자동화가 아니라 수동 도구입니다.

세 가지 판단이 들어가 있습니다.

  1. **증분으로 받습니다.** 매 주기마다 10년치를 다시 내려받으면 요청량이
     수백 배가 되고 무료 소스는 조용히 차단합니다. 마지막 저장 일자에서
     며칠 겹치는 구간만 다시 받습니다(휴장·정정으로 생긴 구멍을 메우려면
     겹침이 필요합니다).

  2. **기본값은 꺼짐입니다.** 앱을 켜자마자 외부로 나가는 네트워크 호출은
     사용자가 명시적으로 선택해야 합니다.

  3. **실패해도 루프는 죽지 않습니다.** 네트워크는 원래 실패합니다. 한 번의
     예외로 스케줄러가 멈추면 사용자는 "자동 갱신이 켜져 있는데 왜 최신이
     아니지"를 영원히 알 수 없습니다. 실패는 상태로 남기고 다음 주기에 다시
     시도합니다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.prefs import get_prefs
from app.store import StoreLocked, get_store

log = logging.getLogger(__name__)

# 루프가 설정 변경을 알아채는 주기. 설정에서 주기를 60분 -> 5분으로 바꿨을 때
# 60분을 기다려야 반영된다면 사용자는 기능이 고장난 줄 압니다.
TICK_SECONDS = 30


@dataclass
class MarketRefresh:
    market: str
    at: str
    ok: bool
    rows: int = 0
    tickers: int = 0
    detail: str = ""


@dataclass
class RefreshState:
    running: bool = False
    last_started_at: str | None = None
    last_finished_at: str | None = None
    next_run_at: str | None = None
    results: list[MarketRefresh] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "next_run_at": self.next_run_at,
            "results": [vars(r) for r in self.results],
        }


state = RefreshState()
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def last_stored_date(market: str):
    with get_store().cursor() as con:
        row = con.execute(
            "SELECT max(date) FROM prices WHERE market = ?", [market]
        ).fetchone()
    return row[0] if row else None


def refresh_market(market: str, *, lookback_days: int = 7) -> MarketRefresh:
    """시장 하나를 증분 갱신. **블로킹 호출** -- 스레드에서 부르십시오."""
    from app.api.analysis import invalidate_forecast_cache
    from app.ingest.pipeline import ingest_kr_prices, ingest_us_prices
    from app.providers.base import ProviderError

    market = market.upper()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        if market == "US":
            last = last_stored_date("US")
            # 저장된 데이터가 없으면 증분이 성립하지 않습니다. 첫 수집은
            # 전체 기간으로 돌려야 예측(최소 800행)이 가능해집니다.
            since = (last - timedelta(days=lookback_days)) if last else None
            res = ingest_us_prices(
                since=since,
                with_classification=since is None,
            )
        else:
            res = ingest_kr_prices(days=max(lookback_days, 5), max_days_per_run=30)
    except ProviderError as exc:
        return MarketRefresh(market, now, False, detail=str(exc))
    except StoreLocked as exc:
        return MarketRefresh(market, now, False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 -- 루프를 죽이지 않습니다
        log.exception("자동 갱신 실패: %s", market)
        return MarketRefresh(market, now, False, detail=f"{type(exc).__name__}: {exc}")

    if res.rows:
        invalidate_forecast_cache(market)
    return MarketRefresh(
        market=market,
        at=now,
        ok=res.rows > 0,
        rows=res.rows,
        tickers=res.tickers,
        detail=(
            f"{res.start} ~ {res.end}"
            if res.rows
            else (res.warnings[0] if res.warnings else "새 데이터가 없습니다.")
        ),
    )


async def run_once(markets: list[str] | None = None) -> list[MarketRefresh]:
    """수동/자동 공용 실행 경로.

    락을 잡는 이유: 자동 주기와 "지금 갱신" 버튼이 겹치면 DuckDB 쓰기가
    충돌합니다. 두 번째 호출은 기다렸다가 실행됩니다.
    """
    prefs = get_prefs()
    targets = markets if markets is not None else prefs.auto_refresh_markets
    async with _lock:
        state.running = True
        state.last_started_at = datetime.now(UTC).isoformat(timespec="seconds")
        results: list[MarketRefresh] = []
        try:
            for market in targets:
                results.append(
                    await asyncio.to_thread(
                        refresh_market,
                        market,
                        lookback_days=prefs.auto_refresh_lookback_days,
                    )
                )
        finally:
            state.running = False
            state.last_finished_at = datetime.now(UTC).isoformat(timespec="seconds")
            if results:
                state.results = results
        return results


async def _loop() -> None:
    next_due: datetime | None = None
    while True:
        try:
            prefs = get_prefs()
            if not prefs.auto_refresh_enabled or not prefs.auto_refresh_markets:
                next_due = None
                state.next_run_at = None
            else:
                now = datetime.now(UTC)
                interval = timedelta(minutes=prefs.auto_refresh_interval_minutes)
                if next_due is None:
                    # 켠 직후 한 번 돌립니다. 다음 주기까지 기다리면 사용자는
                    # 켰는데 아무 일도 안 일어나는 것으로 봅니다.
                    next_due = now
                if now >= next_due:
                    await run_once(prefs.auto_refresh_markets)
                    next_due = datetime.now(UTC) + interval
                state.next_run_at = next_due.isoformat(timespec="seconds")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- 어떤 실패도 루프를 멈추지 못합니다
            log.exception("자동 갱신 루프 오류")
        await asyncio.sleep(TICK_SECONDS)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
