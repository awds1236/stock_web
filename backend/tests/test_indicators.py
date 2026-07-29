"""지표 정확성 테스트 -- 손으로 계산 가능한 값과 대조합니다."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators import flow, price


# ── 수급 지표 ────────────────────────────────────────────────────────────
class TestConsecutiveNetBuyDays:
    def test_counts_streak_and_resets_on_sell(self):
        net = pd.Series([100, 200, 300, -50, 100, 100, 0, 50])
        result = flow.consecutive_net_buy_days(net)
        # 순매도(-50)와 보합(0)에서 모두 리셋되어야 합니다.
        assert result.tolist() == [1, 2, 3, 0, 1, 2, 0, 1]

    def test_all_selling_is_all_zero(self):
        net = pd.Series([-1, -2, -3])
        assert flow.consecutive_net_buy_days(net).tolist() == [0, 0, 0]

    def test_zero_is_not_a_buy(self):
        """순매수 0은 매수가 아닙니다. 보합을 매집으로 세면 연속일수가 부풀려집니다."""
        net = pd.Series([100, 0, 100])
        assert flow.consecutive_net_buy_days(net).tolist() == [1, 0, 1]


class TestNetBuyRatio:
    def test_basic_ratio(self):
        net = pd.Series([50.0, -25.0])
        value = pd.Series([1000.0, 500.0])
        assert flow.net_buy_ratio(net, value).tolist() == [0.05, -0.05]

    def test_zero_trading_value_is_nan_not_inf(self):
        """거래대금 0을 0으로 나누면 inf 가 되어 랭킹 최상위를 차지합니다."""
        result = flow.net_buy_ratio(pd.Series([100.0]), pd.Series([0.0]))
        assert result.isna().all()

    def test_normalizes_away_size_bias(self):
        """대형주 100억과 중형주 100억은 다른 사건임을 이 지표가 구분해야 합니다."""
        net = pd.Series([1e10, 1e10])
        value = pd.Series([1e12, 1e11])  # 대형주 거래대금이 10배
        ratio = flow.net_buy_ratio(net, value)
        assert ratio.iloc[1] == pytest.approx(10 * ratio.iloc[0])


class TestFlowPersistence:
    def test_all_buying_is_one(self):
        net = pd.Series([10.0] * 30)
        assert flow.flow_persistence(net, window=20).iloc[-1] == pytest.approx(1.0)

    def test_all_selling_is_minus_one(self):
        net = pd.Series([-10.0] * 30)
        assert flow.flow_persistence(net, window=20).iloc[-1] == pytest.approx(-1.0)

    def test_alternating_is_near_zero(self):
        net = pd.Series([10.0, -10.0] * 15)
        assert flow.flow_persistence(net, window=20).iloc[-1] == pytest.approx(0.0)

    def test_catches_streak_broken_by_one_day(self):
        """연속일수는 0으로 리셋되지만 지속성은 높게 유지되어야 합니다.

        이것이 두 지표를 모두 두는 이유입니다.
        """
        net = pd.Series([10.0] * 19 + [-10.0])
        assert flow.consecutive_net_buy_days(net).iloc[-1] == 0
        assert flow.flow_persistence(net, window=20).iloc[-1] > 0.8


class TestAmihudIlliquidity:
    def test_illiquid_stock_scores_higher(self):
        """같은 가격 변동이라도 거래대금이 작으면 비유동성이 높아야 합니다."""
        returns = pd.Series([0.02, -0.02] * 15)
        liquid = flow.amihud_illiquidity(returns, pd.Series([1e12] * 30), window=20)
        illiquid = flow.amihud_illiquidity(returns, pd.Series([1e9] * 30), window=20)
        assert illiquid.iloc[-1] > liquid.iloc[-1]

    def test_zero_volume_does_not_produce_inf(self):
        result = flow.amihud_illiquidity(
            pd.Series([0.01] * 30), pd.Series([0.0] * 30), window=20
        )
        assert not np.isinf(result.dropna()).any()


class TestNetBuyStreakValue:
    def test_accumulates_within_streak_only(self):
        net = pd.Series([100.0, 200.0, -50.0, 300.0])
        assert flow.net_buy_streak_value(net).tolist() == [100.0, 300.0, 0.0, 300.0]


# ── 가격 지표 ────────────────────────────────────────────────────────────
class TestRsi:
    def test_monotonic_rise_is_100(self):
        """손실이 없으면 RSI 는 100 입니다."""
        close = pd.Series(np.arange(100, 140, dtype=float))
        assert price.rsi(close, 14).iloc[-1] == pytest.approx(100.0)

    def test_monotonic_fall_is_0(self):
        close = pd.Series(np.arange(140, 100, -1, dtype=float))
        assert price.rsi(close, 14).iloc[-1] == pytest.approx(0.0, abs=1e-9)

    def test_bounded_0_to_100(self):
        rng = np.random.default_rng(4)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500))))
        r = price.rsi(close, 14).dropna()
        assert r.min() >= 0.0 and r.max() <= 100.0

    def test_warmup_period_is_nan(self):
        """워밍업 구간에 값을 내놓으면 백테스트 초기에 가짜 신호가 생깁니다."""
        close = pd.Series(np.arange(100, 130, dtype=float))
        assert price.rsi(close, 14).iloc[:13].isna().all()


class TestMacd:
    def test_hist_equals_line_minus_signal(self):
        rng = np.random.default_rng(8)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200))))
        m = price.macd(close)
        pd.testing.assert_series_equal(
            m["hist"], m["macd"] - m["signal"], check_names=False
        )

    def test_flat_price_gives_zero_macd(self):
        close = pd.Series([100.0] * 100)
        assert price.macd(close)["macd"].dropna().abs().max() == pytest.approx(0.0)


class TestMomentum:
    def test_12_1_skips_recent_month(self):
        """최근 1개월 급등은 12-1 모멘텀에 반영되지 않아야 합니다."""
        close = pd.Series([100.0] * 273)
        close.iloc[-21:] = 200.0  # 최근 21거래일만 2배
        mom = price.momentum_12_1(close, lookback=252, skip=21)
        assert mom.iloc[-1] == pytest.approx(0.0)

    def test_captures_older_gain(self):
        close = pd.Series(np.linspace(100, 200, 300))
        assert price.momentum_12_1(close).iloc[-1] > 0


class TestPctFrom52wHigh:
    def test_at_high_is_zero(self):
        close = pd.Series(np.linspace(100, 200, 300))
        assert price.pct_from_52w_high(close).iloc[-1] == pytest.approx(0.0)

    def test_below_high_is_negative(self):
        close = pd.Series(list(np.linspace(100, 200, 260)) + [150.0] * 40)
        assert price.pct_from_52w_high(close).iloc[-1] == pytest.approx(-0.25, abs=0.01)


class TestAtr:
    def test_constant_range_gives_that_range(self):
        high = pd.Series([110.0] * 100)
        low = pd.Series([100.0] * 100)
        close = pd.Series([105.0] * 100)
        assert price.atr(high, low, close, 14).iloc[-1] == pytest.approx(10.0, rel=0.01)


class TestBollinger:
    def test_pct_b_at_mid_is_half(self):
        rng = np.random.default_rng(12)
        close = pd.Series(100 + rng.normal(0, 1, 200))
        b = price.bollinger(close)
        assert b["pct_b"].dropna().between(-0.5, 1.5).mean() > 0.95

    def test_flat_price_has_zero_width(self):
        b = price.bollinger(pd.Series([100.0] * 50))
        assert b["upper"].dropna().sub(b["lower"].dropna()).abs().max() == pytest.approx(0.0)


class TestByTicker:
    def test_sorts_by_date_before_rolling(self):
        """날짜가 뒤섞인 입력에서도 결과가 정렬된 입력과 같아야 합니다.

        정렬을 빠뜨리면 rolling 계산이 조용히 틀립니다 -- 예외도 안 납니다.
        """
        dates = pd.date_range("2024-01-01", periods=6)
        df = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "ticker": ["A"] * 6 + ["B"] * 6,
                "net": [1.0, 2.0, -1.0, 3.0, 4.0, 5.0] * 2,
            }
        )
        ordered = flow.by_ticker(df, flow.consecutive_net_buy_days, value_cols="net")

        shuffled = df.sample(frac=1.0, random_state=1)
        got = flow.by_ticker(shuffled, flow.consecutive_net_buy_days, value_cols="net")

        pd.testing.assert_series_equal(
            got.sort_index(), ordered.sort_index(), check_names=False
        )

    def test_does_not_leak_across_tickers(self):
        """A종목의 매집 이력이 B종목의 연속일수에 섞이면 안 됩니다."""
        dates = pd.date_range("2024-01-01", periods=4)
        df = pd.DataFrame(
            {
                "date": list(dates) * 2,
                "ticker": ["A"] * 4 + ["B"] * 4,
                "net": [1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0],
            }
        )
        result = flow.by_ticker(df, flow.consecutive_net_buy_days, value_cols="net")
        assert result[df["ticker"] == "A"].tolist() == [1, 2, 3, 4]
        assert result[df["ticker"] == "B"].tolist() == [0, 0, 0, 0]
