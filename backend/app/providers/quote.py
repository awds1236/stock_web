"""현재가 조회 -- 화면이 스스로 갱신되게 만드는 최소 조각.

정직하게 짚어둘 것 두 가지:

  1. **이것은 실시간 시세가 아닙니다.** yfinance 가 주는 값은 지연 시세이며
     (미국 무료 소스는 통상 15분 지연), 거래에 쓸 수 있는 품질이 아닙니다.
     그래서 응답에 `is_live` 와 `note` 를 함께 실어 보내고, 화면은 이것을
     반드시 표시해야 합니다.

  2. **한국은 장중 현재가 소스가 없습니다.** KRX Open API 는 일별 확정
     데이터만 제공합니다. 없는 것을 있는 척하는 대신 저장된 마지막 종가를
     `source="stored"` 로 돌려주고, 왜 장중 값이 없는지 설명합니다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveQuote:
    price: float | None
    previous_close: float | None
    currency: str | None
    source: str  # "live" | "unavailable"
    note: str


DELAY_NOTE = (
    "무료 소스의 지연 시세입니다(통상 15분 이상 지연). 참고용이며 체결 가격이 "
    "아닙니다."
)

KR_NOTE = (
    "한국은 무료로 쓸 수 있는 장중 현재가 소스가 없습니다. KRX Open API 는 "
    "장 마감 후 확정 일별 데이터만 제공하므로, 저장된 마지막 종가를 표시합니다."
)


def fetch_us_quote(ticker: str) -> LiveQuote:
    """yfinance 지연 시세. 실패는 예외가 아니라 '없음'으로 돌려줍니다.

    현재가 조회가 실패했다고 종목 화면 전체가 깨지면 안 됩니다 -- 차트와 지표는
    저장된 데이터만으로 완전히 동작하며, 현재가는 부가 정보입니다.
    """
    try:
        import yfinance as yf
    except ImportError:
        return LiveQuote(None, None, None, "unavailable", "yfinance 가 설치되지 않았습니다.")

    try:
        info = yf.Ticker(ticker).fast_info
        price = _pick(info, "last_price", "lastPrice")
        prev = _pick(info, "previous_close", "previousClose")
        currency = _pick(info, "currency", default=None, numeric=False)
    except Exception as exc:  # noqa: BLE001 -- 비공식 스크래핑이라 실패 형태가 다양합니다
        return LiveQuote(None, None, None, "unavailable", f"현재가 조회 실패: {exc}")

    if price is None:
        return LiveQuote(None, None, None, "unavailable", "응답에 가격이 없습니다.")
    return LiveQuote(price, prev, currency, "live", DELAY_NOTE)


def _pick(info, *keys: str, default=None, numeric: bool = True):
    """fast_info 는 버전에 따라 dict 이기도 하고 속성 객체이기도 합니다."""
    for key in keys:
        value = None
        try:
            value = info[key]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            value = getattr(info, key, None)
        if value is None:
            continue
        if not numeric:
            return str(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default
