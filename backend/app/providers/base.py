"""데이터 소스 추상화.

Phase 0 스파이크 결과에 따라 종목별 연기금 수급의 출처가 KRX Open API가 될 수도,
KRX 정보데이터시스템(MDC)이 될 수도 있습니다. 그 분기를 이 계층에서만 흡수하고
지표·백테스트·API 계층은 소스를 전혀 모르게 만듭니다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

# KRX 투자자 구분. '연기금' 은 연기금·공제회·국가/지자체 등을 포함하는 집계
# 카테고리이며 국민연금 단독 수치가 아닙니다. 이 구분을 국민연금과 동일시하면
# 해석이 틀어지므로 UI에도 동일한 문구를 노출합니다.
INVESTOR_PENSION = "연기금"
INVESTOR_FOREIGN = "외국인"
INVESTOR_INSTITUTION = "기관합계"
INVESTOR_RETAIL = "개인"

INVESTOR_TYPES = (
    INVESTOR_PENSION,
    INVESTOR_FOREIGN,
    INVESTOR_INSTITUTION,
    INVESTOR_RETAIL,
)


@runtime_checkable
class PriceProvider(Protocol):
    """일별 시세 소스."""

    def fetch_daily_prices(self, trade_date: date) -> pd.DataFrame:
        """해당 일자 전 종목 시세.

        Returns:
            columns = [date, ticker, name, market, open, high, low, close,
                       volume, value, market_cap, shares]
        """
        ...


@runtime_checkable
class FlowProvider(Protocol):
    """투자자별 수급 소스.

    중요: 여기서 반환되는 데이터는 **장 마감 후 확정치**입니다. 장중 실시간
    연기금 종목별 수급은 거래소가 원천 제공하지 않으므로 이 인터페이스는
    의도적으로 일자 단위만 받습니다.
    """

    def fetch_daily_flows(self, trade_date: date) -> pd.DataFrame:
        """해당 일자 전 종목 × 투자자 구분 수급.

        Returns:
            columns = [date, ticker, investor_type, buy_value, sell_value, net_value]
            (금액 단위: 원)
        """
        ...


class ProviderError(RuntimeError):
    """데이터 소스 호출 실패."""


class CallBudgetExceeded(ProviderError):
    """일일 호출 한도 초과. 조용히 빈 데이터를 반환하는 것보다 터지는 편이 낫습니다."""
