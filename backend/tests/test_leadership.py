"""섹터 주도권·순환.

이 모듈은 이 앱에서 **지어내기 가장 쉬운 답**을 다룹니다 ("다음 주도 섹터가
어디인가"). 그래서 테스트의 무게중심이 다른 곳과 다릅니다:

    거짓 양성이 나오지 않는가  ← 가장 중요합니다
    진짜 관계는 찾아내는가
    근거가 없을 때 비우는가

다중검정 보정이 없으면 업종 30개에서 순서쌍 870개 중 40여 개가 우연히
유의하게 나옵니다. 그것을 "관련 섹터"라고 화면에 띄우면 이 기능은 난수
발생기가 됩니다. 그래서 귀무 패널을 여러 개 만들어 거짓 양성률을 직접 잽니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.predict import leadership as ld

SECTORS = ["반도체", "은행", "조선", "자동차", "화학", "건설"]


def synth(
    seed: int,
    *,
    lead_lag: bool = False,
    momentum: bool = False,
    n_days: int = 900,
    per_sector: int = 8,
) -> pd.DataFrame:
    """공통 시장요인 + 업종요인 + 종목잡음.

    `lead_lag=True` 면 반도체(t) → 자동차(t+5거래일) 전파를 **실제로 심습니다**.
    심은 것을 찾아내지 못하면 검정력이 없는 것이고, 심지 않은 것을 찾아내면
    데이터 마이닝입니다. 둘 다 확인합니다.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-06-01", periods=n_days)
    market = rng.normal(0.0003, 0.009, n_days)
    factor = {s: rng.normal(0, 0.008, n_days) for s in SECTORS}
    if lead_lag:
        factor["자동차"][5:] += 0.55 * factor["반도체"][:-5]
    if momentum:
        for s in SECTORS:
            f = factor[s].copy()
            for i in range(21, n_days):
                f[i] += 0.35 * float(np.mean(factor[s][i - 21 : i]))
            factor[s] = f

    rows = []
    for si, s in enumerate(SECTORS):
        for k in range(per_sector):
            r = market + factor[s] + rng.normal(0, 0.010, n_days)
            c = 50_000 * np.exp(np.cumsum(r))
            rows.append(
                pd.DataFrame(
                    {
                        "ticker": f"{si}{k:02d}",
                        "date": dates,
                        "sector": s,
                        "industry": s,
                        "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                        "volume": 1e5, "value": 1e5 * c,
                    }
                )
            )
    return pd.concat(rows, ignore_index=True)


NULL = [synth(100 + i) for i in range(6)]
PLANTED = synth(7, lead_lag=True)


# ── 다중검정 보정 ─────────────────────────────────────────────────────────
class TestFalsePositives:
    """근거 없는 관계를 만들어내지 않는가 -- 이 모듈의 존재 근거."""

    @pytest.mark.parametrize("panel", NULL)
    def test_no_significant_pair_under_the_null(self, panel):
        out = ld.lead_lag(panel)
        assert out["available"]
        assert out["n_significant"] == 0, (
            "관계를 심지 않은 데이터에서 유의한 쌍이 나왔습니다 -- 보정이 "
            "동작하지 않으면 이 기능은 난수 발생기입니다"
        )

    def test_verdict_says_so_plainly(self):
        out = ld.lead_lag(NULL[0])
        assert "유의한 선행-후행 관계가 없습니다" in out["verdict"]

    def test_threshold_is_far_above_a_naive_cutoff(self):
        """보정 문턱은 개별 p<0.05 문턱보다 확실히 높아야 합니다."""
        out = ld.lead_lag(NULL[0])
        naive = 1.96 / np.sqrt(out["n_weeks"])  # 개별 검정의 대략적 임계 상관
        assert out["significance_threshold"] > naive

    def test_pairs_are_reported_even_when_not_significant(self):
        """숨기면 사용자가 '아무것도 못 찾았다'로 오해합니다. 판정만 붙입니다."""
        out = ld.lead_lag(NULL[0])
        assert out["pairs"], "상위 쌍은 항상 보여주되 significant 로 구분합니다"
        assert all(p["significant"] is False for p in out["pairs"])


class TestPower:
    def test_planted_relation_ranks_first(self):
        out = ld.lead_lag(PLANTED)
        top = out["pairs"][0]
        assert (top["leader"], top["follower"]) == ("반도체", "자동차")

    def test_planted_relation_survives_the_correction(self):
        out = ld.lead_lag(PLANTED)
        hit = [
            p for p in out["pairs"]
            if p["leader"] == "반도체" and p["follower"] == "자동차"
        ]
        assert hit and hit[0]["significant"]

    def test_self_pairs_are_excluded(self):
        """자기 자신과의 시차 상관은 자기상관이지 선행이 아닙니다."""
        out = ld.lead_lag(PLANTED)
        assert all(p["leader"] != p["follower"] for p in out["pairs"])

    def test_deterministic_for_the_same_data(self):
        """순열검정에 시드를 고정했습니다. 새로고침마다 결론이 바뀌면 못 씁니다."""
        a = ld.lead_lag(PLANTED)
        b = ld.lead_lag(PLANTED)
        assert a["significance_threshold"] == b["significance_threshold"]
        assert a["pairs"] == b["pairs"]


# ── 지속성 ───────────────────────────────────────────────────────────────
class TestPersistence:
    def test_measures_instead_of_assuming(self):
        out = ld.persistence(NULL[0])
        assert out["available"]
        assert out["rank_ic"] is not None and out["t_stat"] is not None
        assert out["n_periods"] >= 3

    def test_no_momentum_data_gets_a_no_evidence_verdict(self):
        out = ld.persistence(NULL[0])
        assert abs(out["t_stat"]) < 2
        assert "증거가 없습니다" in out["verdict"]

    def test_planted_momentum_moves_the_measure_upward(self):
        """t 가 2 를 넘지 않아도 됩니다 -- 방향이 맞고 과대주장하지 않으면 됩니다."""
        with_mom = ld.persistence(synth(11, momentum=True))
        without = ld.persistence(synth(11))
        assert with_mom["rank_ic"] > without["rank_ic"]

    def test_short_history_refuses_instead_of_guessing(self):
        out = ld.persistence(synth(3, n_days=80))
        assert out["available"] is False
        assert out["reason"]

    def test_periods_do_not_overlap(self):
        """겹치는 구간으로 t 값을 만들면 유의성이 부풀려집니다."""
        n_days, horizon = 900, 21
        out = ld.persistence(synth(5), horizon=horizon)
        max_periods = n_days // horizon
        assert out["n_periods"] <= max_periods


# ── 주도 업종 ─────────────────────────────────────────────────────────────
class TestLeaders:
    def test_reports_breadth_and_participation_next_to_return(self):
        """수익률만 보여주면 소수 종목이 끌어올린 업종이 '주도'가 됩니다."""
        out = ld.leaders(NULL[0])
        assert out["available"]
        for row in out["leading"]:
            assert "breadth" in row and "participation_20d" in row
            assert row["n_constituents"] > 0

    def test_ordered_by_score_and_leading_beats_lagging(self):
        out = ld.leaders(NULL[0])
        scores = [r["leadership_score"] for r in out["leading"]]
        assert scores == sorted(scores, reverse=True)
        assert out["leading"][0]["leadership_score"] >= (
            out["lagging"][0]["leadership_score"]
        )

    def test_score_is_labelled_as_unvalidated(self):
        out = ld.leaders(NULL[0])
        assert "검증된 지표가 아니" in out["score_note"]

    def test_leading_and_lagging_do_not_overlap(self):
        out = ld.leaders(NULL[0], top=3)
        assert not ({r["sector"] for r in out["leading"]} &
                    {r["sector"] for r in out["lagging"]})

    def test_short_history_refuses(self):
        out = ld.leaders(synth(3, n_days=20))
        assert out["available"] is False


# ── 순환 후보 ─────────────────────────────────────────────────────────────
class TestRotationCandidates:
    def test_no_significant_pair_means_no_candidates(self):
        """근거가 없을 때 비우는 것이 이 기능의 핵심입니다."""
        panel = NULL[0]
        out = ld.rotation_candidates(
            ld.leaders(panel), ld.persistence(panel), ld.lead_lag(panel)
        )
        assert out["available"] is False
        assert out["candidates"] == []
        assert "지어내는 대신" in out["what_this_means"]

    def test_candidates_are_always_conditional(self):
        panel = synth(21, lead_lag=True)
        lead_, persist, ll = (
            ld.leaders(panel), ld.persistence(panel), ld.lead_lag(panel)
        )
        out = ld.rotation_candidates(lead_, persist, ll)
        for c in out["candidates"]:
            assert "유지된다면" in c["condition"]
            assert c["led_by"] and c["corr"]

    def test_candidates_only_come_from_significant_pairs(self):
        panel = synth(21, lead_lag=True)
        ll = ld.lead_lag(panel)
        out = ld.rotation_candidates(ld.leaders(panel), ld.persistence(panel), ll)
        sig = {(p["leader"], p["follower"]) for p in ll["pairs"] if p["significant"]}
        for c in out["candidates"]:
            assert (c["led_by"], c["sector"]) in sig

    def test_already_moved_sectors_are_not_candidates(self):
        """'다음'을 묻는데 이미 오른 업종을 내놓으면 답이 아닙니다."""
        panel = synth(21, lead_lag=True)
        lead_ = ld.leaders(panel)
        rows = {r["sector"]: r for r in lead_["leading"] + lead_["lagging"]}
        ex = [r["excess_20d"] for r in rows.values() if r["excess_20d"] is not None]
        median = float(np.median(ex))
        out = ld.rotation_candidates(lead_, ld.persistence(panel), ld.lead_lag(panel))
        for c in out["candidates"]:
            assert c["excess_20d"] is None or c["excess_20d"] <= median

    def test_persistence_verdict_travels_with_the_candidates(self):
        """두 근거가 어긋나면 사용자가 알아야 합니다."""
        panel = synth(21, lead_lag=True)
        out = ld.rotation_candidates(
            ld.leaders(panel), ld.persistence(panel), ld.lead_lag(panel)
        )
        assert out.get("persistence_verdict") or out.get("reason")


# ── 리포트 조립 ───────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def store_with_data(tmp_path_factory):
    from app import reports
    from app.store import Store

    store = Store(path=tmp_path_factory.mktemp("ld") / "s.duckdb")
    panel = synth(31, lead_lag=True).assign(name="종목", market_cap=1e12, shares=1e6)
    store.upsert_prices("KR", panel)
    reports.clear_panel_cache()
    return store


class TestLeadershipReport:
    def test_assembles_all_three_tiers(self, store_with_data):
        from app import reports

        out = reports.leadership_report("KR", store=store_with_data)
        for key in ("leaders", "persistence", "lead_lag", "rotation", "market_regime"):
            assert key in out
        assert out["market"] == "KR"
        assert out["n_universe"] > 0

    def test_caveats_separate_observation_from_inference(self, store_with_data):
        from app import reports

        joined = " ".join(reports.leadership_report("KR", store=store_with_data)["caveats"])
        assert "관측이지만" in joined and "다중검정" in joined

    def test_lead_lag_is_cached_by_data_stamp(self, store_with_data):
        """순열검정 400회를 업종마다 다시 돌리면 정적 배포가 수십 초 느려집니다."""
        import time

        from app import reports

        reports.clear_panel_cache()
        t0 = time.time()
        reports.leadership_report("KR", store=store_with_data)
        first = time.time() - t0
        t1 = time.time()
        reports.leadership_report("KR", store=store_with_data)
        second = time.time() - t1
        assert second < first, "두 번째 호출이 캐시를 타지 않았습니다"

    def test_missing_market_raises_lookup_error(self, store_with_data):
        from app import reports

        with pytest.raises(LookupError):
            reports.leadership_report("US", store=store_with_data)


class TestMarketRegime:
    def test_reports_concentration_dispersion_and_correlation(self, store_with_data):
        from app import reports

        r = reports.market_report("KR", store=store_with_data)["regime"]
        assert r["concentration"]["ex_top5_ret_20d"] is not None
        assert r["dispersion_20d"] is not None
        assert -1.0 <= r["avg_pair_correlation_60d"] <= 1.0
        assert r["summary"]

    def test_correlation_is_high_when_a_common_factor_dominates(self):
        """공통 시장요인을 크게 주면 평균 쌍상관이 올라가야 합니다."""
        import pathlib
        import tempfile

        from app import reports
        from app.store import Store

        tmp = pathlib.Path(tempfile.mkdtemp())
        rng = np.random.default_rng(2)
        n = 300
        dates = pd.bdate_range("2023-01-02", periods=n)
        common = rng.normal(0, 0.02, n)
        rows = []
        for i in range(12):
            r = common + rng.normal(0, 0.002, n)  # 거의 전부 공통요인
            c = 1000 * np.exp(np.cumsum(r))
            rows.append(pd.DataFrame({
                "ticker": f"T{i:02d}", "date": dates, "name": "x",
                "sector": "A", "industry": "A", "open": c, "high": c, "low": c,
                "close": c, "volume": 1.0, "value": 1.0,
                "market_cap": 1e12, "shares": 1e6,
            }))
        s = Store(path=tmp / "c.duckdb")
        s.upsert_prices("KR", pd.concat(rows, ignore_index=True))
        reports.clear_panel_cache()
        r = reports._market_regime(s.prices("KR"))
        assert r["avg_pair_correlation_60d"] > 0.8
        reports.clear_panel_cache()


class TestSectorReportAdditions:
    def test_sector_gets_regime_leadlag_and_concentration(self, store_with_data):
        from app import reports

        reports.clear_panel_cache()
        out = reports.sector_report("KR", "반도체", store=store_with_data)
        assert out["regime"]["trend"]["label"]
        assert "led_by" in out["lead_lag"] and "leads" in out["lead_lag"]
        assert out["concentration"]["available"] is True

    def test_sector_regime_warns_that_atr_is_not_comparable(self, store_with_data):
        """업종 지수엔 고가·저가가 없어 ATR 이 좁게 나옵니다. 말해야 합니다."""
        from app import reports

        out = reports.sector_report("KR", "반도체", store=store_with_data)
        assert "ATR" in out["regime"]["caveat"]


# ── 주간 변환 ─────────────────────────────────────────────────────────────
class TestWeekly:
    def test_weeks_do_not_overlap(self):
        wide = ld.sector_wide(NULL[0])
        weekly = ld._weekly(wide)
        assert len(weekly) == len(wide) // ld.WEEK

    def test_weekly_compounding_matches_manual(self):
        wide = ld.sector_wide(NULL[0])
        weekly = ld._weekly(wide)
        col = wide.columns[0]
        tail = wide[col].fillna(0.0).to_numpy()
        trimmed = tail[len(tail) - len(weekly) * ld.WEEK :]
        expected = float(np.prod(1 + trimmed[: ld.WEEK]) - 1)
        assert weekly[col].iloc[0] == pytest.approx(expected, abs=1e-12)
