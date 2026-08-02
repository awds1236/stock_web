"""지지·저항 구간 엔진과 분할 체결 골격.

여기서 지키는 것은 "숫자가 맞다"가 아니라 **"이 숫자로 주문을 걸 수 있다"**
입니다. 지지선이 현재가에 붙어 있거나, 세 단계가 같은 자리에 몰려 있거나,
비중 합이 100% 가 아니면 계산이 아무리 정교해도 쓸 수 없습니다.

확률은 닫힌 해가 있어(반사원리) 정확히 검증할 수 있는 몇 안 되는 부분이라
경계 조건까지 확인합니다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app import plan as pl
from app import reports
from app.indicators import zones as zn


def panel(
    seed: int = 7,
    n: int = 400,
    drift: float = 0.0,
    base: float = 70_000.0,
    vol: float = 0.012,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = base * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": close,
            "high": close * (1 + np.abs(rng.normal(0, 0.008, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.008, n))),
            "close": close,
            "volume": rng.lognormal(14, 0.4, n),
            "value": rng.lognormal(14, 0.4, n) * close,
        }
    )


REGIMES = {
    "횡보": panel(7),
    "상승": panel(3, drift=0.0012),
    "하락": panel(11, drift=-0.0012),
    "달러표기": panel(5, base=182.4),
    "고변동": panel(21, vol=0.03),
    "저변동": panel(31, vol=0.004),
}


# ── 도달 확률 ─────────────────────────────────────────────────────────────
class TestTouchProbability:
    def test_matches_the_reflection_principle(self):
        """닫힌 해가 있으므로 근사가 아니라 정확히 맞아야 합니다."""
        spot, barrier, vol, days = 100.0, 90.0, 0.30, 21
        z = abs(math.log(barrier / spot)) / (vol * math.sqrt(days / 252))
        expected = 2 * 0.5 * math.erfc(z / math.sqrt(2))
        assert zn.touch_probability(spot, barrier, vol, days) == pytest.approx(
            expected, abs=1e-4
        )

    def test_symmetric_in_log_distance(self):
        """무추세 가정에서는 위·아래 같은 로그거리의 확률이 같아야 합니다."""
        up = zn.touch_probability(100, 100 * math.e**0.1, 0.3, 21)
        down = zn.touch_probability(100, 100 * math.e**-0.1, 0.3, 21)
        assert up == pytest.approx(down, abs=1e-6)

    def test_touch_beats_closing_beyond(self):
        """터치 확률은 '종가로 넘길 확률'의 두 배입니다 -- 장중 체결을 반영합니다."""
        p_touch = zn.touch_probability(100, 90, 0.3, 21)
        z = abs(math.log(0.9)) / (0.3 * math.sqrt(21 / 252))
        p_close_beyond = 0.5 * math.erfc(z / math.sqrt(2))
        # 구현이 4자리로 반올림해 돌려주므로 그만큼의 여유를 둡니다.
        assert p_touch == pytest.approx(2 * p_close_beyond, abs=1e-4)

    def test_monotone_in_distance_and_in_time(self):
        near = zn.touch_probability(100, 98, 0.3, 21)
        far = zn.touch_probability(100, 80, 0.3, 21)
        longer = zn.touch_probability(100, 80, 0.3, 63)
        assert near > far
        assert longer > far

    def test_bounded_and_never_exceeds_one(self):
        """가격이 거의 붙어 있으면 2Φ(-ε) 가 1 을 넘습니다. 잘라내야 합니다."""
        assert zn.touch_probability(100, 100.0001, 0.3, 21) <= 1.0

    @pytest.mark.parametrize(
        "args",
        [(0, 90, 0.3, 21), (100, 0, 0.3, 21), (100, 90, 0, 21), (100, 90, 0.3, 0),
         (100, 90, float("nan"), 21)],
    )
    def test_degenerate_inputs_return_none_not_garbage(self, args):
        assert zn.touch_probability(*args) is None

    def test_sigma_days_is_the_time_for_a_one_sigma_move(self):
        # 연 30% 변동성에서 10% 이동이 1σ 가 되는 시점
        d = zn.sigma_days(100, 100 * math.e**0.1, 0.30)
        assert d == pytest.approx(252 * (0.1 / 0.30) ** 2, rel=1e-3)


# ── 구간 ─────────────────────────────────────────────────────────────────
class TestZones:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_at_most_three_per_side_and_correctly_classified(self, name):
        z = zn.price_zones(REGIMES[name])
        assert len(z["supports"]) <= 3 and len(z["resistances"]) <= 3
        spot = z["spot"]
        assert all(s["price"] < spot for s in z["supports"])
        assert all(r["price"] > spot for r in z["resistances"])

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_ordered_from_nearest_to_furthest(self, name):
        """1차·2차·3차라는 이름이 붙으므로 순서가 곧 계약입니다."""
        z = zn.price_zones(REGIMES[name])
        sup = [s["price"] for s in z["supports"]]
        res = [r["price"] for r in z["resistances"]]
        assert sup == sorted(sup, reverse=True)
        assert res == sorted(res)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_band_contains_its_representative_price(self, name):
        z = zn.price_zones(REGIMES[name])
        for s in z["supports"] + z["resistances"]:
            assert s["low"] <= s["price"] <= s["high"]
            assert s["low"] < s["high"]

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_bands_do_not_straddle_the_current_price(self, name):
        """지지 구간이 현재가 위로 넘어가면 '지지'라는 말이 뜻을 잃습니다."""
        z = zn.price_zones(REGIMES[name])
        spot = z["spot"]
        assert all(s["high"] <= spot + 1e-6 for s in z["supports"])
        assert all(r["low"] >= spot - 1e-6 for r in z["resistances"])

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_zones_are_separated_enough_to_be_a_ladder(self, name):
        """0.3 ATR 간격 세 개면 한 번의 하락에 셋 다 체결됩니다 -- 분할이 아닙니다."""
        z = zn.price_zones(REGIMES[name])
        atr = z["atr_14"]
        for side in ("supports", "resistances"):
            prices = [s["price"] for s in z[side]]
            for a, b in zip(prices, prices[1:], strict=False):
                assert abs(a - b) >= 0.6 * atr - 1e-6

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_zones_are_not_glued_to_the_current_price(self, name):
        z = zn.price_zones(REGIMES[name])
        floor = max(0.5 * z["atr_14"], 0.012 * z["spot"])
        for s in z["supports"] + z["resistances"]:
            assert abs(s["price"] - z["spot"]) >= floor - 1e-6

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_band_width_stays_usable(self, name):
        """폭이 5% 를 넘으면 '구간'이 아니라 그냥 넓은 영역입니다."""
        z = zn.price_zones(REGIMES[name])
        for s in z["supports"] + z["resistances"]:
            assert (s["high"] - s["low"]) / s["price"] <= 0.05

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_probability_decays_with_distance(self, name):
        z = zn.price_zones(REGIMES[name])
        for side in ("supports", "resistances"):
            probs = [s["touch_prob_21d"] for s in z[side]]
            assert all(p is None or 0 <= p <= 1 for p in probs)
            usable = [p for p in probs if p is not None]
            assert usable == sorted(usable, reverse=True)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_every_zone_carries_its_evidence(self, name):
        """근거 없는 선은 이 앱이 그리지 않기로 한 것입니다."""
        z = zn.price_zones(REGIMES[name])
        for s in z["supports"] + z["resistances"]:
            assert s["evidence"], "근거 배열이 비어 있으면 검증할 수 없습니다"
            assert s["n_methods"] == len(s["methods"]) >= 1
            assert s["confidence"] in ("높음", "보통", "낮음")

    def test_confluence_outranks_a_single_method(self):
        z = zn.price_zones(REGIMES["상승"])
        multi = [s for s in z["supports"] + z["resistances"] if s["n_methods"] >= 3]
        single = [s for s in z["supports"] + z["resistances"] if s["n_methods"] == 1]
        if multi and single:
            assert max(s["score"] for s in multi) > min(s["score"] for s in single)

    def test_method_notes_expose_the_evidence_grades(self):
        """어떤 방법을 얼마나 믿는지가 응답에 없으면 사용자는 알 방법이 없습니다."""
        notes = zn.price_zones(REGIMES["횡보"])["method_notes"]
        by_name = {n["method"]: n for n in notes}
        assert by_name["거래량 밀집대"]["evidence_weight"] > (
            by_name["주요 이동평균"]["evidence_weight"]
        ), "이동평균이 거래량 밀집대와 같은 무게로 취급되면 안 됩니다"
        assert all(n["basis"] for n in notes)

    def test_short_history_says_so_instead_of_guessing(self):
        z = zn.price_zones(panel(1, n=30))
        assert z["insufficient"] is True
        assert z["supports"] == [] and z["resistances"] == []

    def test_missing_volume_still_produces_zones(self):
        """거래대금은 없을 수 있습니다 (일부 소스). 그때도 나머지 방법은 살아야 합니다."""
        df = REGIMES["횡보"].drop(columns=["value", "volume"])
        z = zn.price_zones(df)
        assert not z["insufficient"]
        assert z["supports"] or z["resistances"]
        used = {m for s in z["supports"] + z["resistances"] for m in s["methods"]}
        assert "거래량 밀집대" not in used


# ── 분할 골격 ─────────────────────────────────────────────────────────────
def built(name: str) -> tuple[dict, dict]:
    z = zn.price_zones(REGIMES[name])
    p = pl.scaled_plan(
        spot=z["spot"],
        supports=z["supports"],
        resistances=z["resistances"],
        atr=z["atr_14"],
    )
    return z, p


class TestEntryLadder:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_weights_sum_to_exactly_one(self, name):
        """99.99% 나 100.01% 는 화면에서 계산 오류로 보입니다."""
        _, p = built(name)
        assert sum(s["weight"] for s in p["entry"]["steps"]) == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_immediate_tranche_matches_the_no_fill_probability(self, name):
        """지정가만 걸면 못 사는 경우가 있습니다. 그 확률만큼을 즉시 체결로 둡니다."""
        _, p = built(name)
        entry = p["entry"]
        first = next((s for s in entry["steps"] if s["kind"] == "market"), None)
        if entry["prob_no_limit_fill_21d"] is None:
            return
        assert first is not None
        assert first["weight"] == pytest.approx(entry["prob_no_limit_fill_21d"], abs=1e-3)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_deeper_steps_get_lower_weight(self, name):
        """도달 확률 비례이므로, 먼 구간에 더 크게 걸리면 규칙이 깨진 것입니다."""
        _, p = built(name)
        limits = [s for s in p["entry"]["steps"] if s["kind"] == "limit"]
        weights = [s["weight"] for s in limits]
        assert weights == sorted(weights, reverse=True)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_average_cost_falls_as_more_steps_fill(self, name):
        _, p = built(name)
        costs = [f["avg_cost"] for f in p["entry"]["partial_fills"]]
        assert costs == sorted(costs, reverse=True)
        assert p["entry"]["partial_fills"][-1]["capital_used"] == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_average_cost_sits_between_the_extremes(self, name):
        z, p = built(name)
        avg = p["entry"]["avg_cost_if_all_filled"]
        prices = [s["price"] for s in p["entry"]["steps"]]
        assert min(prices) <= avg <= max(prices)
        assert avg <= z["spot"], "평균단가가 현재가보다 높으면 분할 매수가 아닙니다"


class TestInvalidationAndRisk:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_invalidation_sits_below_every_support(self, name):
        """마지막 지지 위에 두면 정상적인 눌림에도 계획이 폐기됩니다."""
        z, p = built(name)
        inv = p["invalidation"]["price"]
        assert inv > 0
        for s in z["supports"]:
            assert inv < s["low"]

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_risk_is_expressed_as_a_position_cap(self, name):
        _, p = built(name)
        risk = p["risk"]
        assert 0 < risk["risk_per_position_pct"] < 1
        cap = risk["max_position_for_1pct_account_risk"]
        assert 0 < cap <= 1
        # 비중은 4자리로 반올림되므로 곱이 정확히 0.01 은 아닙니다.
        assert cap == 1.0 or cap * risk["risk_per_position_pct"] == pytest.approx(
            0.01, rel=1e-3
        )

    def test_reward_to_risk_is_reported_even_when_unfavourable(self):
        """1 미만이어도 숨기지 않습니다. 숨기면 화면이 광고가 됩니다."""
        ratios = [built(n)[1]["risk"]["reward_to_risk"] for n in REGIMES]
        assert any(r is not None for r in ratios)
        assert all(r is None or r > 0 for r in ratios)


class TestExitLadder:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_unsold_remainder_is_stated(self, name):
        """전량 매도로 보이게 만들면 사용자가 남은 물량을 잊습니다."""
        _, p = built(name)
        e = p["exit"]
        assert e["weight_sold_if_all_reached"] + e["weight_still_held"] == pytest.approx(
            1.0, abs=1e-3
        )
        assert 0 <= e["weight_still_held"] <= 1

    @pytest.mark.parametrize("name", list(REGIMES))
    def test_exit_prices_are_above_the_current_price(self, name):
        z, p = built(name)
        for s in p["exit"]["steps"]:
            assert s["price"] > z["spot"]

    def test_downtrend_holds_more_than_an_uptrend(self):
        """하락 추세에서는 저항이 멀어 사다리가 덜 체결되는 게 정상입니다.

        절대 수치를 고정하면 난수 시드에 묶인 테스트가 됩니다. 두 국면의
        **상대 관계**가 규칙이 요구하는 성질입니다.
        """
        _, down = built("하락")
        _, up = built("상승")
        assert down["exit"]["weight_still_held"] > up["exit"]["weight_still_held"]


class TestPlanHonesty:
    def test_caveats_state_that_splitting_does_not_raise_returns(self):
        _, p = built("횡보")
        joined = " ".join(p["caveats"])
        assert "기대수익을 높이지 않습니다" in joined
        assert "추천이 아니라" in joined

    def test_unusable_input_is_refused_rather_than_faked(self):
        out = pl.scaled_plan(spot=float("nan"), supports=[], resistances=[], atr=1.0)
        assert out["available"] is False
        out2 = pl.scaled_plan(spot=100.0, supports=[], resistances=[], atr=1.0)
        assert out2["available"] is False


# ── 국면 · 시나리오 ───────────────────────────────────────────────────────
class TestRegime:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_labels_are_always_present(self, name):
        r = reports._regime(REGIMES[name])
        assert r["trend"]["label"]
        assert r["volatility"]["label"]
        assert r["summary"]

    def test_direction_of_the_trend_label_follows_the_data(self):
        assert reports._regime(REGIMES["상승"])["trend"]["label"] == "상승 추세"
        assert reports._regime(REGIMES["하락"])["trend"]["label"] == "하락 추세"

    def test_volatility_percentile_is_a_probability(self):
        v = reports._regime(REGIMES["고변동"])["volatility"]
        assert 0.0 <= v["percentile_2y"] <= 1.0

    def test_range_position_is_between_zero_and_one(self):
        rp = reports._regime(REGIMES["횡보"])["range_position"]
        assert 0.0 <= rp["value"] <= 1.0
        assert rp["low"] < rp["high"]

    def test_short_history_does_not_crash(self):
        r = reports._regime(panel(1, n=30))
        assert r["trend"]["label"] == "판정 불가"


class TestScenarios:
    @pytest.mark.parametrize("name", list(REGIMES))
    def test_every_scenario_is_conditional_and_bounded(self, name):
        z = zn.price_zones(REGIMES[name])
        scen = reports._scenarios(z, reports._regime(REGIMES[name]))
        assert scen
        for s in scen:
            assert s["trigger"] and s["then"]
            assert s["touch_prob"] is None or 0 <= s["touch_prob"] <= 1
            assert s["horizon_days"] == z["horizon_days"]

    def test_range_hold_probability_is_flagged_as_a_lower_bound(self):
        """상방·하방은 겹칠 수 있으므로 1-p-p 는 하한입니다. 그렇게 밝혀야 합니다."""
        z = zn.price_zones(REGIMES["횡보"])
        scen = reports._scenarios(z, reports._regime(REGIMES["횡보"]))
        hold = next(s for s in scen if s["name"] == "현 구간 유지")
        assert "하한" in hold["prob_note"]

    def test_no_zones_means_no_invented_scenarios(self):
        z = zn.price_zones(panel(1, n=30))
        assert reports._scenarios(z, {"trend": {"label": "판정 불가"}}) == []
