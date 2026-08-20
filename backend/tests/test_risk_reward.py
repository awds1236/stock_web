"""손익비와 거래비용 노출.

주문을 넣는 데 쓰는 숫자라 틀리면 바로 돈입니다. 특히 셋:

    * risk = 평균 매수단가 - 무효화 가격. 부호가 뒤집히거나 0 이면 포지션
      사이징이 무한대가 됩니다.
    * R:R 은 비용 **전** 값입니다. 화면이 비용 후 값과 섞어 보여주면 실제보다
      낙관적인 비율을 보게 됩니다.
    * 비용률이 응답에 실려야 브라우저가 실효 단가를 계산할 수 있습니다.
      빠지면 주문서가 수수료·거래세 0 인 세계의 숫자가 됩니다.
"""

from __future__ import annotations

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


class TestRiskReward:
    def test_risk_is_the_distance_from_average_entry_to_invalidation(self):
        buy = ld.build_plan("buy", 100.0, [], atr_value=5.0)  # 95, 90, 85 -> 평균 90
        sell = ld.build_plan("sell", 100.0, [], atr_value=5.0)  # 105, 110, 115 -> 110
        rr = ld.risk_reward(buy, sell)
        assert rr["avg_buy"] == pytest.approx(90.0)
        assert rr["stop"] == pytest.approx(80.0)
        assert rr["risk_per_share"] == pytest.approx(10.0)
        assert rr["risk_pct"] == pytest.approx(10.0 / 90.0)

    def test_reward_is_the_distance_to_the_average_exit(self):
        buy = ld.build_plan("buy", 100.0, [], atr_value=5.0)
        sell = ld.build_plan("sell", 100.0, [], atr_value=5.0)
        rr = ld.risk_reward(buy, sell)
        assert rr["avg_sell"] == pytest.approx(110.0)
        assert rr["reward_per_share"] == pytest.approx(20.0)
        assert rr["rr"] == pytest.approx(2.0)

    def test_no_invalidation_means_no_risk_number(self):
        """무효화 가격이 없으면 R 을 정의할 수 없습니다 -- 0 으로 두면 안 됩니다."""
        empty = ld.build_plan("buy", 0.0, [], atr_value=1.0)
        assert ld.risk_reward(empty, empty) == {}

    def test_rr_is_none_when_the_exit_is_below_the_entry(self):
        """저항이 평균 매수단가보다 아래면 이 계획에는 수익 구간이 없습니다."""
        buy = ld.build_plan("buy", 100.0, [], atr_value=5.0)
        sell = ld.build_plan("sell", 100.0, [], atr_value=5.0)
        broken = ld.LadderPlan(
            side="sell",
            weighting="equal",
            last_close=100.0,
            tranches=sell.tranches,
            full_fill_avg_price=80.0,
        )
        rr = ld.risk_reward(buy, broken)
        assert rr["reward_per_share"] < 0
        assert rr["rr"] is None, "음수 손익비를 숫자로 보여주면 비율처럼 읽힙니다"

    def test_pyramid_changes_the_risk_because_the_average_moves(self):
        equal = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="equal")
        pyramid = ld.build_plan("buy", 100.0, [], atr_value=5.0, weighting="pyramid")
        sell = ld.build_plan("sell", 100.0, [], atr_value=5.0)
        assert (
            ld.risk_reward(pyramid, sell)["risk_per_share"]
            < ld.risk_reward(equal, sell)["risk_per_share"]
        ), "더 싼 회차에 더 실으면 평균이 손절선에 가까워집니다"


class TestLadderPayload:
    def _series(self):
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(3)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.015, 300))))
        return close, close * 1.01, close * 0.99

    def test_costs_are_included_so_the_browser_can_compute_net_prices(self):
        from app.markets import cost_model_for

        close, high, low = self._series()
        out = ld.build_ladders(
            close, high, low, _levels(float(close.iloc[-1]), []), costs=cost_model_for("KR")
        )
        assert out["costs"]["sell_tax"] > 0, "한국 매도에는 증권거래세가 붙습니다"
        assert out["costs"]["commission"] >= 0
        assert out["costs"]["slippage"] > 0

    def test_risk_block_exists_for_every_weighting(self):
        close, high, low = self._series()
        out = ld.build_ladders(close, high, low, [])
        assert set(out["risk"]) == set(ld.WEIGHTINGS)
        for w in ld.WEIGHTINGS:
            assert out["risk"][w]["rr"] is not None

    def test_average_daily_value_rides_along_for_the_fill_check(self):
        close, high, low = self._series()
        out = ld.build_ladders(close, high, low, [], avg_daily_value=1.23e10)
        assert out["avg_daily_value"] == pytest.approx(1.23e10)

    def test_missing_costs_are_absent_not_zero(self):
        """비용을 0 으로 채우면 화면이 수수료 없는 세계의 숫자를 보여줍니다."""
        close, high, low = self._series()
        out = ld.build_ladders(close, high, low, [])
        assert "costs" not in out


class TestPromptIsDecisive:
    """프롬프트가 판단을 요구하지 않으면 모델은 양비론으로 도망갑니다."""

    def test_system_asks_for_a_stance(self):
        from app.ai.prompts import SYSTEM

        assert "판단을 내리십시오" in SYSTEM
        assert "회피" in SYSTEM and "관망" in SYSTEM

    def test_system_still_forbids_inventing_numbers(self):
        """판단을 허용하는 것과 숫자를 지어내게 두는 것은 다릅니다."""
        from app.ai.prompts import SYSTEM

        assert "지어내지" in SYSTEM
        assert "이 앱의 데이터에는" in SYSTEM

    def test_stock_prompt_leads_with_the_call_and_ends_with_invalidation(self):
        from app.ai import prompts

        text = prompts.stock_prompt({"market": "KR", "ticker": "005930", "ladder": {}})
        heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
        assert heads[0] == "## 판단"
        assert "## 이 판단이 틀리는 경우" in heads
        assert "## 데이터 신뢰도" in heads

    def test_stock_prompt_requires_the_risk_reward_number(self):
        from app.ai import prompts

        text = prompts.stock_prompt({"market": "KR", "ticker": "005930", "ladder": {}})
        assert "rr" in text and "risk_per_share" in text

    def test_sector_and_market_prompts_also_take_a_position(self):
        from app.ai import prompts

        for text in (
            prompts.sector_prompt({"market": "KR", "sector": "반도체"}),
            prompts.market_prompt({"market": "KR"}),
        ):
            assert text.splitlines()[text.splitlines().index("## 판단")] == "## 판단"


class TestApiKeepsTheFields:
    """pydantic 응답 모델에 없는 필드는 **조용히 사라집니다.**

    실제로 costs/risk 를 계산해 놓고 LadderOut 에 선언하지 않아, 응답에서
    통째로 빠진 적이 있습니다. 화면은 그때 비용률을 기본값으로 되돌려
    수수료·거래세가 다른 세계의 주문서를 그렸습니다.
    """

    def test_stock_detail_carries_costs_risk_and_liquidity(self, tmp_path, monkeypatch):
        from conftest import make_panel
        from fastapi.testclient import TestClient

        from app.api import analysis as api
        from app.main import app
        from app.store import Store

        store = Store(path=tmp_path / "api.duckdb")
        panel = make_panel(n_days=300, n_tickers=4, seed=9)
        panel["name"] = "테스트"
        store.upsert_prices("KR", panel)
        monkeypatch.setattr(api, "get_store", lambda: store)

        ticker = sorted(panel["ticker"].unique())[0]
        body = TestClient(app).get(f"/api/stocks/KR/{ticker}").json()
        ladder = body["ladder"]
        assert ladder["costs"] is not None, "비용률이 빠지면 주문서가 낙관적이 됩니다"
        assert ladder["costs"]["sell_tax"] > 0
        assert ladder["avg_daily_value"] is not None
        assert ladder["risk"]["equal"]["risk_per_share"] > 0
