"""AI 분석 실행 -- 리포트 계산 → 프롬프트 → 호출 → 로그.

세분화의 이유:
    "전부 분석"은 느리고 비쌉니다. 유니버스 300종목에 대해 AI 를 돌리면
    시간도 비용도 사용자가 감당할 수 없습니다. 그래서 분석 단위를 셋으로
    나누고, **각각 버튼 하나에 대응**시킵니다.

        stock  -- 지금 보고 있는 종목 하나
        sector -- 지금 보고 있는 업종 하나
        market -- 시장 전체 요약 + 주목 종목

    한 번의 호출은 한 대상만 다룹니다. 세 개를 한 번에 물으면 응답이 길어져
    각각이 얕아지고, 실패하면 셋 다 잃습니다.

실패도 로그에 남깁니다. 호출은 유료이고, "왜 실패했는지"를 나중에 확인할 수
없으면 같은 실패를 반복하게 됩니다.
"""

from __future__ import annotations

from typing import Any

from app import reports
from app.ai import prompts
from app.ai.client import AIError, complete
from app.ai.logs import LogEntry, get_ai_logs
from app.credentials import get_credential
from app.prefs import get_prefs


def ai_available() -> tuple[bool, str]:
    """(사용 가능 여부, 안내 문구). UI 가 버튼을 끌지 결정하는 데 씁니다."""
    if not get_credential("OPENAI_API_KEY"):
        return False, (
            "AI 분석에는 OpenAI API 키가 필요합니다. 설정 화면에서 입력하십시오. "
            "키 없이도 시세·지표·예측은 모두 동작합니다."
        )
    return True, ""


def run_stock_analysis(market: str, ticker: str) -> LogEntry:
    market = market.upper()
    report = reports.stock_report(market, ticker)
    label = report.get("name") or ticker
    forecast = _forecast_slice(market, ticker)
    facts: dict[str, Any] = dict(report)
    if forecast:
        # 로그에도 남깁니다. 나중에 "그때 무엇을 보고 그렇게 썼나"를 되짚을 때
        # 예측 블록이 빠져 있으면 서술의 절반이 근거 없이 남습니다.
        facts["forecast"] = forecast
    return _run(
        kind="stock",
        market=market,
        subject=ticker,
        subject_label=f"{label} ({ticker})",
        facts=facts,
        user_prompt=prompts.stock_prompt(report, forecast),
    )


def _forecast_slice(market: str, ticker: str) -> dict | None:
    """이미 계산된 워크포워드 예측에서 이 종목 몫만.

    **없으면 없는 대로 둡니다.** 여기서 새로 계산하면 AI 버튼 한 번이 수십 초
    멈추고, 사용자는 AI 가 느린 것으로 오해합니다. 품질 지표는 유니버스 전체
    기준이라 종목을 잘라내도 그대로 유효합니다.
    """
    try:
        from app.api.analysis import peek_forecast
    except Exception:  # noqa: BLE001 -- 예측 모듈이 없어도 서술은 되어야 합니다
        return None
    out = peek_forecast(market, "direction", 21)
    if out is None:
        return None
    latest = [r for r in out.latest if r.get("ticker") == ticker]
    if not latest:
        return None
    return {
        "target": out.target,
        "horizon_days": out.horizon_days,
        "this_stock": latest[0],
        "quality": out.quality.model_dump(),
        "caveat": out.caveat,
    }


def run_sector_analysis(market: str, sector: str, *, level: str = "industry") -> LogEntry:
    market = market.upper()
    report = reports.sector_report(market, sector, level=level)
    return _run(
        kind="sector",
        market=market,
        subject=sector,
        subject_label=sector,
        facts=report,
        user_prompt=prompts.sector_prompt(report),
    )


def run_market_analysis(market: str) -> LogEntry:
    market = market.upper()
    report = reports.market_report(market)
    return _run(
        kind="market",
        market=market,
        subject=market,
        subject_label="한국 시장 전체" if market == "KR" else "미국 시장 전체",
        facts=report,
        user_prompt=prompts.market_prompt(report),
    )


def run_rotation_analysis(market: str) -> LogEntry:
    """섹터 주도권·순환. 시장 분석과 **분리한** 이유는 근거의 성격이 달라서입니다.

    시장 분석은 관측된 현황을 서술합니다. 주도권 분석은 거기에 더해 "다음은
    어디인가"라는 추론을 다루므로, 통과해야 할 검증(지속성 측정, 다중검정 보정)이
    따로 있습니다. 한 프롬프트에 섞으면 근거 강도가 다른 문장들이 같은 어조로
    나열되고, 사용자는 어디까지가 사실인지 구분할 수 없게 됩니다.
    """
    market = market.upper()
    report = reports.leadership_report(market)
    return _run(
        kind="rotation",
        market=market,
        subject=market,
        subject_label="한국 섹터 주도권" if market == "KR" else "미국 섹터 주도권",
        facts=report,
        user_prompt=prompts.rotation_prompt(report),
    )


def _run(
    *,
    kind: str,
    market: str,
    subject: str,
    subject_label: str,
    facts: dict[str, Any],
    user_prompt: str,
) -> LogEntry:
    prefs = get_prefs()
    logs = get_ai_logs()
    api_key = get_credential("OPENAI_API_KEY")

    try:
        resp = complete(
            api_key=api_key,
            base_url=prefs.ai_base_url,
            model=prefs.ai_model,
            system=prompts.SYSTEM,
            user=user_prompt,
            max_output_tokens=prefs.ai_max_output_tokens,
            timeout=prefs.ai_timeout_seconds,
        )
    except AIError as exc:
        logs.add(
            kind=kind,  # type: ignore[arg-type]
            market=market,
            subject=subject,
            subject_label=subject_label,
            model=prefs.ai_model,
            content=None,
            facts=facts,
            error=str(exc),
        )
        raise

    entry_id = logs.add(
        kind=kind,  # type: ignore[arg-type]
        market=market,
        subject=subject,
        subject_label=subject_label,
        model=resp.model,
        content=resp.content,
        facts=facts,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        duration_ms=resp.duration_ms,
    )
    entry = logs.get(entry_id)
    assert entry is not None  # 방금 넣었습니다
    return entry
