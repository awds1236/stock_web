"""테스트용 합성 패널 생성기.

실제 시장 데이터로는 '엔진이 옳은가'를 검증할 수 없습니다. 정답을 모르기
때문입니다. 통계적 성질을 우리가 지정한 합성 데이터를 써야 이론값과 대조할 수
있습니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_panel(
    n_days: int = 400,
    n_tickers: int = 30,
    seed: int = 42,
    daily_vol: float = 0.02,
    drift: float = 0.0,
    trading_value: float = 1e12,
) -> pd.DataFrame:
    """랜덤워크 시가 패널.

    거래대금 기본값을 매우 크게 잡아 거래량 제약이 기본적으로 바인딩되지 않게
    합니다(제약은 별도 테스트에서 검증). 그래야 다른 테스트가 제약 때문에
    실패하는 혼선을 피할 수 있습니다.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    tickers = [f"{i:06d}" for i in range(n_tickers)]

    shocks = rng.normal(drift, daily_vol, size=(n_days, n_tickers))
    prices = 10_000 * np.exp(np.cumsum(shocks, axis=0))

    opens = pd.DataFrame(prices, index=dates, columns=tickers)
    panel = (
        opens.stack()
        .rename("open")
        .reset_index()
        .rename(columns={"level_0": "date", "level_1": "ticker"})
    )
    panel["close"] = panel["open"]
    # 실제 시세 패널이 갖는 컬럼을 모두 채웁니다. 최소 스키마로 두면 지표·특성
    # 코드가 컬럼 부재 경로로만 검증되어, 정상 경로의 버그를 놓칩니다.
    panel["high"] = panel["open"]
    panel["low"] = panel["open"]
    panel["value"] = trading_value
    panel["volume"] = panel["value"] / panel["open"]
    panel["market_cap"] = panel["open"] * 1e6
    panel["shares"] = 1e6
    return panel


def open_to_open_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """엔진과 동일한 수익률 정의: open[d+1]/open[d] - 1."""
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    return opens.shift(-1) / opens - 1


def attach_signal(panel: pd.DataFrame, signal: pd.DataFrame, name: str) -> pd.DataFrame:
    """date × ticker 행렬을 패널에 시그널 컬럼으로 붙입니다."""
    long = signal.stack(future_stack=True).rename(name).reset_index()
    long.columns = ["date", "ticker", name]
    out = panel.merge(long, on=["date", "ticker"], how="left")
    return out


@pytest.fixture
def panel() -> pd.DataFrame:
    return make_panel()


@pytest.fixture
def zero_cost():
    from app.config import CostModel

    return CostModel(sell_tax=0.0, commission=0.0, slippage=0.0, max_participation=1.0)
