"""분할 매수·매도 구간.

이 모듈이 하는 일은 산술뿐이므로, 테스트도 산술을 고정합니다. 특히 세 가지가
틀리면 화면의 숫자가 조용히 거짓말을 합니다:

    * 누적 평균단가가 비중을 반영하지 않으면 -- 사용자는 '3분할하면 평균단가가
      이만큼 내려간다'를 그 숫자로 판단합니다.
    * 회차가 서로 붙어 있으면 -- 이름만 분할이고 실제로는 한 가격입니다.
    * 변동성으로 채운 회차가 지지선처럼 보이면 -- 없는 근거를 있는 것으로
      읽게 됩니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators import ladder as ld
from app.indicators.levels import Level


def _levels(last_close: float, prices: list[float], touches: int = 3) -> list[Level]:
    return [
        Level(
            price=p,
            kind="support" if p <= last_close else "resistance",
            touches=touches,
            distance_pct=p / last_close - 1,
        )
        for p in prices
    ]


def _series(n: int = 300, base: float = 100.0, vol: float = 0.015):
    rng = np.random.default_rng(11)
    close = pd.Series(base * np.exp(np.cumsum(rng.normal(0, vol, n))))
    return close, close * 1.01, close * 0.99


class TestShape:
    def test_at_least_three_tranches_even_without_enough_levels(self):
        """지지 후보가 하나뿐이어도 3분할은 나와야 합니다."""
        plan = ld.build_plan("buy", 100.0, _levels(100.0, [95.0]), atr_value=3.0)
        assert len(plan.tranches) == 3
        assert [t.step for t in plan.tranches] == [1, 2, 3]

    def test_missing_tranches_are_marked_as_volatility_not_levels(self):
        """없는 지지선을 지어내면 안 됩니다 -- 근거의 종류를 바꿔 표시합니다."""
        plan = ld.build_plan("buy", 100.0, _levels(100.0, [95.0]), atr_value=3.0)
        assert [t.basis for t in plan.tranches] == ["level", "volatility", "volatility"]
        assert plan.n_level_based == 1
        assert plan.n_volatility_based == 2
        assert all(t.touches is None for t in plan.tranches if t.basis == "volatility")

    def test_steps_are_clamped_to_the_minimum(self):
        plan = ld.build_plan("buy", 100.0, _levels(100.0, [95.0]), atr_value=3.0, steps=1)
        assert len(plan.tranches) == ld.MIN_STEPS

    def test_steps_are_clamped_to_the_maximum(self):
        plan = ld.build_plan("buy", 100.0, _levels(100.0, [95.0]), atr_value=3.0, steps=99)
        assert len(plan.tranches) == ld.MAX_STEPS


class TestDirection:
    def test_buy_tranches_go_down_from_the_current_price(self):
        plan = ld.build_plan(
            "buy", 100.0, _levels(100.0, [95.0, 90.0, 85.0, 110.0]), atr_value=2.0
        )
        prices = [t.price for t in plan.tranches]
        assert prices == sorted(prices, reverse=True)
        assert all(p < 100.0 for p in prices)

    def test_sell_tranches_go_up_from_the_current_price(self):
        plan = ld.build_plan(
            "sell", 100.0, _levels(100.0, [105.0, 112.0, 120.0, 90.0]), atr_value=2.0
        )
        prices = [t.price for t in plan.tranches]
        assert prices == sorted(prices)
        assert all(p > 100.0 for p in prices)

    def test_levels_on_the_wrong_side_are_ignored(self):
        """저항을 매수 구간에 넣으면 '현재가보다 비싸게 분할 매수'가 됩니다."""
        plan = ld.build_plan("buy", 100.0, _levels(100.0, [130.0, 140.0]), atr_value=2.0)
        assert plan.n_level_based == 0
        assert all(t.price < 100.0 for t in plan.tranches)


class TestSpacing:
    def test_tranches_are_not_stacked_on_top_of_each_other(self):
        """0.3% 간격의 '3분할'은 사실상 한 가격에 거는 것입니다."""
        plan = ld.build_plan(
            "buy", 100.0, _levels(100.0, [99.8, 99.7, 99.5, 92.0]), atr_value=1.0
        )
        prices = [t.price for t in plan.tranches]
        gaps = [prices[i] - prices[i + 1] for i in range(len(prices) - 1)]
        assert all(g >= 2.0 - 1e-9 for g in gaps), f"간격이 너무 촘촘합니다: {gaps}"

    def test_spacing_floor_applies_when_atr_is_tiny(self):
        """ATR 이 비정상적으로 작아도 현재가의 2% 아래로는 붙지 않습니다."""
        plan = ld.build_plan("buy", 100.0, [], atr_value=0.01)
        prices = [t.price for t in plan.tranches]
        assert prices == pytest.approx([98.0, 96.0, 94.0])

    def test_spacing_follows_atr_when_it_is_large(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=8.0)
        prices = [t.price for t in plan.tranches]
        assert prices == pytest.approx([92.0, 84.0, 76.0])


class TestArithmetic:
    def test_equal_weights_sum_to_one(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="equal")
        assert sum(t.weight for t in plan.tranches) == pytest.approx(1.0)
        assert plan.tranches[-1].cum_weight == pytest.approx(1.0)

    def test_pyramid_puts_more_on_later_tranches(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="pyramid")
        weights = [t.weight for t in plan.tranches]
        assert weights == pytest.approx([1 / 6, 2 / 6, 3 / 6])
        assert sum(weights) == pytest.approx(1.0)

    def test_average_price_is_the_weighted_mean_of_filled_tranches(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="equal")
        prices = [t.price for t in plan.tranches]  # 95, 90, 85
        assert plan.tranches[0].avg_price == pytest.approx(prices[0])
        assert plan.tranches[1].avg_price == pytest.approx(np.mean(prices[:2]))
        assert plan.tranches[2].avg_price == pytest.approx(np.mean(prices))
        assert plan.full_fill_avg_price == pytest.approx(np.mean(prices))

    def test_pyramid_lowers_the_buy_average_versus_equal(self):
        """뒤 회차(더 싼 가격)에 더 실으면 평균단가는 내려갑니다 -- 산술입니다."""
        equal = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="equal")
        pyramid = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="pyramid")
        assert pyramid.full_fill_avg_price < equal.full_fill_avg_price

    def test_average_vs_close_is_negative_for_buys(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0)
        assert plan.full_fill_avg_vs_close_pct < 0
        assert all(t.avg_vs_close_pct < 0 for t in plan.tranches)

    def test_average_vs_close_is_positive_for_sells(self):
        plan = ld.build_plan("sell", 100.0, [], atr_value=5.0)
        assert plan.full_fill_avg_vs_close_pct > 0

    def test_unknown_weighting_falls_back_to_equal(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="nonsense")
        assert plan.weighting == "equal"
        assert all(t.weight == pytest.approx(1 / 3) for t in plan.tranches)


class TestInvalidation:
    def test_buy_invalidation_sits_below_the_last_tranche(self):
        plan = ld.build_plan("buy", 100.0, [], atr_value=5.0)
        assert plan.invalidation is not None
        assert plan.invalidation < plan.tranches[-1].price
        assert plan.invalidation_pct is not None and plan.invalidation_pct < 0

    def test_sell_invalidation_sits_above_the_last_tranche(self):
        plan = ld.build_plan("sell", 100.0, [], atr_value=5.0)
        assert plan.invalidation is not None
        assert plan.invalidation > plan.tranches[-1].price


class TestDegenerate:
    def test_zero_price_yields_an_empty_plan_not_a_crash(self):
        """KRX 는 거래정지 종목에 종가 0 을 줍니다 -- 나눗셈이 폭발하면 안 됩니다."""
        plan = ld.build_plan("buy", 0.0, [], atr_value=1.0)
        assert plan.tranches == []
        assert plan.full_fill_avg_price is None

    def test_tranche_prices_never_go_negative(self):
        plan = ld.build_plan("buy", 3.0, [], atr_value=2.0, steps=5)
        assert all(t.price > 0 for t in plan.tranches)


class TestBuildLadders:
    def test_both_sides_and_both_weightings_are_returned(self):
        close, high, low = _series()
        out = ld.build_ladders(close, high, low, [])
        assert set(out["buy"]) == set(ld.WEIGHTINGS)
        assert set(out["sell"]) == set(ld.WEIGHTINGS)
        assert out["steps"] == ld.DEFAULT_STEPS
        assert out["caveat"]

    def test_every_plan_has_at_least_three_tranches_on_real_shaped_data(self):
        close, high, low = _series()
        out = ld.build_ladders(close, high, low, [])
        for side in ("buy", "sell"):
            for weighting in ld.WEIGHTINGS:
                assert len(out[side][weighting]["tranches"]) >= 3

    def test_empty_series_does_not_crash(self):
        empty = pd.Series(dtype=float)
        out = ld.build_ladders(empty, empty, empty, [])
        assert out["buy"] == {}


class TestReportAndApi:
    def test_stock_report_carries_the_ladder(self, tmp_path, monkeypatch):
        from conftest import make_panel

        from app import reports
        from app.store import Store

        store = Store(path=tmp_path / "l.duckdb")
        panel = make_panel(n_days=300, n_tickers=4, seed=5)
        panel["name"] = "테스트"
        store.upsert_prices("US", panel)
        monkeypatch.setattr("app.store._store", store)
        monkeypatch.setattr(reports, "get_store", lambda: store)

        ticker = sorted(panel["ticker"].unique())[0]
        report = reports.stock_report("US", ticker)
        assert "ladder" in report
        assert len(report["ladder"]["buy"]["equal"]["tranches"]) >= 3
        assert len(report["ladder"]["sell"]["equal"]["tranches"]) >= 3

    def test_ai_prompt_demands_at_least_three_tranches(self):
        """프롬프트가 요구하지 않으면 모델은 한두 줄로 뭉뚱그립니다."""
        from app.ai import prompts

        text = prompts.stock_prompt({"market": "KR", "ticker": "005930", "ladder": {}})
        assert "## 분할 대응 구간" in text
        assert "최소 3회차" in text
        assert "volatility" in text, "변동성으로 채운 회차를 구분하라는 지시가 있어야 합니다"
        assert "invalidation" in text
