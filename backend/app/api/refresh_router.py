"""자동 갱신 상태·수동 실행 API.

설정(주기·대상 시장)은 `/api/settings/preferences` 가 담당하고, 여기서는
**지금 어떤 상태인가**와 **지금 한 번 돌려라**만 다룹니다. 설정과 상태를 같은
엔드포인트에 섞으면, 화면이 상태를 폴링할 때마다 설정 전체를 함께 받게 됩니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import refresh
from app.markets import MARKETS
from app.prefs import get_prefs

router = APIRouter(prefix="/api/refresh", tags=["refresh"])


class RefreshResultOut(BaseModel):
    market: str
    at: str
    ok: bool
    rows: int
    tickers: int
    detail: str


class RefreshStatusOut(BaseModel):
    enabled: bool
    interval_minutes: int
    markets: list[str]
    lookback_days: int
    running: bool
    last_started_at: str | None
    last_finished_at: str | None
    next_run_at: str | None
    results: list[RefreshResultOut]
    note: str


NOTE = (
    "증분 갱신입니다 -- 마지막 저장 일자에서 며칠 겹치는 구간만 다시 받습니다. "
    "일별 확정 데이터를 다루므로 장중에 돌려도 그날 종가는 마감 후에야 "
    "채워집니다."
)


@router.get("/status", response_model=RefreshStatusOut)
def status() -> RefreshStatusOut:
    prefs = get_prefs()
    snap = refresh.state.snapshot()
    return RefreshStatusOut(
        enabled=prefs.auto_refresh_enabled,
        interval_minutes=prefs.auto_refresh_interval_minutes,
        markets=prefs.auto_refresh_markets,
        lookback_days=prefs.auto_refresh_lookback_days,
        note=NOTE,
        **snap,
    )


@router.post("/run", response_model=list[RefreshResultOut])
async def run_now(
    market: str | None = Query(None, description="비우면 설정된 대상 시장 전부"),
) -> list[RefreshResultOut]:
    """지금 한 번 갱신. 자동 갱신이 꺼져 있어도 동작합니다."""
    if market is not None and market.upper() not in MARKETS:
        raise HTTPException(404, f"알 수 없는 시장: {market}")
    targets = [market.upper()] if market else None
    results = await refresh.run_once(targets)
    return [RefreshResultOut(**vars(r)) for r in results]
