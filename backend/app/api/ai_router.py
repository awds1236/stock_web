"""AI 분석 API.

설계 규칙 두 가지:

  * **분석 실행은 POST 입니다.** GET 이면 브라우저 프리페치나 새로고침이
    유료 호출을 일으킵니다. 돈이 나가는 동작은 사용자가 버튼을 눌렀을 때만
    실행되어야 합니다.
  * **결과는 항상 로그에 들어간 뒤 반환됩니다.** 응답만 주고 저장을 나중으로
    미루면, 브라우저를 닫은 순간 유료로 만든 결과가 사라집니다.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.ai.client import AIError
from app.ai.logs import get_ai_logs
from app.ai.service import (
    ai_available,
    run_market_analysis,
    run_rotation_analysis,
    run_sector_analysis,
    run_stock_analysis,
)
from app.markets import MARKETS
from app.prefs import get_prefs

router = APIRouter(prefix="/api/ai", tags=["ai"])


class LogOut(BaseModel):
    id: str
    created_at: str
    kind: str
    market: str
    subject: str
    subject_label: str | None
    model: str | None
    content: str | None
    facts: dict | None = None
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int | None
    error: str | None


class AIStatusOut(BaseModel):
    configured: bool
    model: str
    base_url: str
    n_logs: int
    message: str
    disclaimer: str


DISCLAIMER = (
    "AI 서술은 이 앱이 계산한 숫자만 근거로 생성되며, 실적·뉴스·공시를 보지 "
    "않습니다. 모델은 같은 데이터에도 매번 다르게 서술하고 틀린 추론을 할 수 "
    "있습니다. 숫자 자체는 지표 화면이 원본이며, AI 문장이 그것과 어긋나면 "
    "지표 화면을 믿으십시오."
)


@router.get("/status", response_model=AIStatusOut)
def status() -> AIStatusOut:
    ok, message = ai_available()
    prefs = get_prefs()
    return AIStatusOut(
        configured=ok,
        model=prefs.ai_model,
        base_url=prefs.ai_base_url,
        n_logs=get_ai_logs().count(),
        message=message,
        disclaimer=DISCLAIMER,
    )


@router.post("/analyze/stock/{market}/{ticker}", response_model=LogOut)
def analyze_stock(market: str, ticker: str) -> LogOut:
    _require_market(market)
    return _run(lambda: run_stock_analysis(market, ticker))


@router.post("/analyze/sector/{market}", response_model=LogOut)
def analyze_sector(
    market: str,
    sector: str = Query(..., description="업종명"),
    level: str = Query("industry", pattern="^(sector|industry)$"),
) -> LogOut:
    _require_market(market)
    return _run(lambda: run_sector_analysis(market, sector, level=level))


@router.post("/analyze/market/{market}", response_model=LogOut)
def analyze_market(market: str) -> LogOut:
    _require_market(market)
    return _run(lambda: run_market_analysis(market))


@router.post("/analyze/rotation/{market}", response_model=LogOut)
def analyze_rotation(market: str) -> LogOut:
    """섹터 주도권·순환. 시장 분석과 별도 버튼인 이유는 근거의 성격이 달라서입니다."""
    _require_market(market)
    return _run(lambda: run_rotation_analysis(market))


@router.get("/logs", response_model=list[LogOut])
def list_logs(
    kind: str | None = Query(None, pattern="^(stock|sector|market|rotation)$"),
    market: str | None = None,
    subject: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LogOut]:
    """분석 이력. 본문은 미리보기이며 전문은 `/logs/{id}` 에서 가져갑니다."""
    entries = get_ai_logs().list(
        kind=kind, market=market, subject=subject, limit=limit, offset=offset
    )
    return [LogOut(**{**asdict(e), "facts": None}) for e in entries]


@router.get("/logs/{entry_id}", response_model=LogOut)
def get_log(entry_id: str) -> LogOut:
    entry = get_ai_logs().get(entry_id)
    if entry is None:
        raise HTTPException(404, "해당 분석 기록이 없습니다.")
    return LogOut(**asdict(entry))


@router.delete("/logs/{entry_id}")
def delete_log(entry_id: str) -> dict:
    if not get_ai_logs().delete(entry_id):
        raise HTTPException(404, "해당 분석 기록이 없습니다.")
    return {"deleted": entry_id}


def _run(fn) -> LogOut:
    try:
        entry = fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AIError as exc:
        # 409 는 '설정이 필요하다', 나머지는 공급자 응답을 그대로 전달합니다.
        # 애매한 500 으로 뭉개면 사용자가 키 문제인지 모델명 문제인지 모릅니다.
        raise HTTPException(exc.status or 502, str(exc)) from exc
    return LogOut(**asdict(entry))


def _require_market(market: str) -> None:
    if market.upper() not in MARKETS:
        raise HTTPException(404, f"알 수 없는 시장: {market}")
