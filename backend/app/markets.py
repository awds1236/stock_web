"""시장 정의 -- 한국/미국의 구조적 차이를 한 곳에 모읍니다.

이 파일이 존재하는 이유는 두 시장의 **수급 데이터 성격이 근본적으로 다르기**
때문입니다. 이 차이를 코드에 명시하지 않으면 앱이 미국 주식에 대해서도 한국과
같은 수준의 수급 분석을 제공하는 것처럼 보이게 됩니다. 그렇지 않습니다.

    한국: 거래소가 **종목별·일별 투자자 유형별** 순매수를 의무 공시합니다.
          학술 문헌이 한국을 이 공시의 예외적 사례로 다룰 만큼 드문 구조입니다.
          (대만 三大法人 이 유사한 구조를 가집니다.)

    미국: 종목별·일별 투자자 유형별 수급이 **공개 자료로 존재하지 않습니다.**
          무료로 얻을 수 있는 가장 가까운 대체물은 SEC Form 4 내부자 매매이며,
          이것은 '기관 수급'이 아니라 '내부자 거래'로 성격이 다릅니다.

따라서 두 시장의 시그널을 나란히 놓고 "어느 쪽이 더 좋다"고 비교하면 안 됩니다.
서로 다른 것을 재고 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FlowKind = Literal["investor_type", "insider", "none"]


@dataclass(frozen=True)
class CostDefaults:
    """시장별 거래비용 기본값 (소수 비율).

    전부 **자리표시자**입니다. 실제 세율·수수료는 자주 바뀌므로 백테스트 전에
    확인해 환경변수로 주입하십시오. 사용된 값은 모든 리포트에 기록됩니다.
    """

    sell_tax: float
    commission: float
    slippage: float
    max_participation: float
    tax_note: str


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    currency: str
    timezone: str
    flow_kind: FlowKind
    flow_label: str
    flow_caveat: str
    costs: CostDefaults


KR = Market(
    code="KR",
    name="한국 (KOSPI/KOSDAQ)",
    currency="KRW",
    timezone="Asia/Seoul",
    flow_kind="investor_type",
    flow_label="투자자별 순매수 (연기금·외국인·기관·개인)",
    flow_caveat=(
        "종목별 확정 수급은 장 마감 후(확정 18:00 이후) 제공됩니다. 장중 '실시간 "
        "기관 수급'은 창구분석 추정치이며 연기금 단독으로 분리되지 않습니다. "
        "또한 '연기금등'은 연기금·공제회·국가/지자체를 포함하는 집계 구분이며 "
        "국민연금 단독 수치가 아닙니다."
    ),
    costs=CostDefaults(
        sell_tax=0.0015,
        commission=0.00015,
        slippage=0.001,
        max_participation=0.01,
        tax_note=(
            "매도 시 증권거래세 + 농어촌특별세. 시장(KOSPI/KOSDAQ)과 연도에 따라 "
            "다르며 최근 수년간 반복적으로 변경되었습니다. 반드시 확인 후 주입."
        ),
    ),
)

US = Market(
    code="US",
    name="미국 (NYSE/NASDAQ)",
    currency="USD",
    timezone="America/New_York",
    flow_kind="insider",
    flow_label="내부자 매매 (SEC Form 4)",
    flow_caveat=(
        "미국은 종목별·일별 투자자 유형별 수급을 공개하지 않습니다. Form 4는 "
        "'기관 수급'이 아니라 임원·이사·10% 주주의 본인 거래이며, 성격이 다릅니다. "
        "매수(코드 P)는 6~12개월 초과수익 4~8%가 일관되게 보고되었으나, 매도는 "
        "예측력이 약하거나 없습니다. 결정적으로, 신호당 거래금액을 현실적 규모로 "
        "제한하면 초과수익이 사라지거나 음수로 뒤집힌다는 연구가 있습니다 -- "
        "이 앱의 거래량 제약과 정확히 같은 문제이며, 가설 H7에서 직접 검증합니다."
    ),
    costs=CostDefaults(
        # 미국은 증권거래세가 없습니다. 매도 시 SEC Section 31 수수료와 FINRA TAF가
        # 붙지만 한국 거래세 대비 두 자릿수 작습니다. 요율은 매년 조정됩니다.
        sell_tax=0.0000278,
        commission=0.0,  # 주요 리테일 브로커 수수료 무료
        slippage=0.0005,  # 대형주 스프레드가 한국보다 좁음
        max_participation=0.01,
        tax_note=(
            "SEC Section 31 수수료 + FINRA TAF. 매년 조정되므로 확인 후 주입. "
            "한국과 달리 증권거래세는 없습니다."
        ),
    ),
)

MARKETS: dict[str, Market] = {m.code: m for m in (KR, US)}


def get_market(code: str) -> Market:
    try:
        return MARKETS[code.upper()]
    except KeyError:
        raise ValueError(
            f"알 수 없는 시장 코드: {code!r}. 사용 가능: {sorted(MARKETS)}"
        ) from None


def cost_model_for(code: str):
    """시장별 CostModel 생성.

    우선순위: 환경변수 `COST_<MARKET>_*` > 시장별 기본값.
    예: `COST_US_SLIPPAGE=0.001`, `COST_KR_SELL_TAX=0.0018`

    시장 접두사를 요구하는 이유는, 한국과 미국의 비용 구조가 자릿수 단위로 다르기
    때문입니다(한국 거래세 0.15% vs 미국 SEC 수수료 0.00278%). 공용 `COST_*`
    변수를 두면 한쪽에 맞춘 값이 다른 쪽에 조용히 적용됩니다.
    """
    import os

    from app.config import CostModel

    market = get_market(code)
    d = market.costs
    prefix = f"COST_{market.code}_"

    def pick(field: str, default: float) -> float:
        raw = os.getenv(prefix + field.upper())
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            raise ValueError(
                f"{prefix}{field.upper()} 값이 숫자가 아닙니다: {raw!r}"
            ) from None

    return CostModel(
        sell_tax=pick("sell_tax", d.sell_tax),
        commission=pick("commission", d.commission),
        slippage=pick("slippage", d.slippage),
        max_participation=pick("max_participation", d.max_participation),
    )
