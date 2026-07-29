"""과최적화 보정 통계 테스트.

이 모듈이 틀리면 '검증했다'는 주장 자체가 무너집니다. 특히 DSR 이 시도 횟수에
반응하지 않으면 다중검정 보정이 이름만 남습니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest import stats


def make_returns(mean: float, vol: float, n: int = 1000, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(rng.normal(mean, vol, n), index=idx)


class TestComputeStats:
    def test_sharpe_matches_definition(self):
        r = make_returns(0.0005, 0.01, 2000)
        s = stats.compute_stats(r)
        expected = (r.mean() * 252) / (r.std(ddof=1) * np.sqrt(252))
        assert s.sharpe == pytest.approx(expected)

    def test_max_drawdown_is_negative_and_correct(self):
        # +100% 후 -50% → MDD 는 -50%
        r = pd.Series([1.0, -0.5], index=pd.bdate_range("2024-01-01", periods=2))
        assert stats.compute_stats(r).max_drawdown == pytest.approx(-0.5)

    def test_monotonic_gains_have_no_drawdown(self):
        r = pd.Series([0.01] * 100, index=pd.bdate_range("2024-01-01", periods=100))
        assert stats.compute_stats(r).max_drawdown == pytest.approx(0.0)

    def test_cagr_compounds_correctly(self):
        # 252거래일 동안 매일 정확히 +0.1% → 연 (1.001^252 - 1)
        r = pd.Series([0.001] * 252, index=pd.bdate_range("2020-01-01", periods=252))
        assert stats.compute_stats(r).cagr == pytest.approx(1.001**252 - 1, rel=1e-6)

    def test_empty_input_does_not_crash(self):
        s = stats.compute_stats(pd.Series(dtype=float))
        assert s.n_days == 0 and s.sharpe == 0.0


class TestDeflatedSharpe:
    def test_decreases_as_trials_increase(self):
        """같은 성과라도 더 많이 시도했다면 신뢰도가 낮아져야 합니다.

        이것이 다중검정 보정의 전부입니다. 이 성질이 없으면 그리드 서치를
        아무리 크게 돌려도 대가를 치르지 않게 됩니다.
        """
        r = make_returns(0.0006, 0.01, 1500, seed=3)
        dsr = [stats.deflated_sharpe_ratio(r, n) for n in (1, 10, 100, 1000)]
        assert dsr == sorted(dsr, reverse=True), f"DSR 이 시도 횟수에 반응하지 않음: {dsr}"
        assert dsr[0] > dsr[-1]

    def test_strong_strategy_survives_modest_trial_count(self):
        r = make_returns(0.002, 0.01, 2000, seed=5)  # 샤프 ~3
        assert stats.deflated_sharpe_ratio(r, n_trials=20) > 0.9

    def test_mediocre_strategy_fails_after_many_trials(self):
        """샤프 0.5짜리를 1000번 시도해서 골랐다면 우연을 배제할 수 없습니다."""
        r = make_returns(0.0003, 0.01, 1000, seed=9)
        assert stats.deflated_sharpe_ratio(r, n_trials=1000) < 0.95

    def test_rejects_zero_trials(self):
        with pytest.raises(ValueError):
            stats.deflated_sharpe_ratio(make_returns(0.001, 0.01), n_trials=0)


class TestProbabilisticSharpe:
    def test_is_calibrated_on_zero_mean_returns(self):
        """무의미한 전략들에 대해 PSR 이 올바르게 보정되어 있는가.

        단일 시드로 검사하면 안 됩니다. 표본평균은 우연히 양수일 수 있고 그때
        PSR 이 0.5 를 크게 넘는 것은 **정상**입니다(PSR 은 관측된 샤프지수가
        표본오차를 넘는지를 재는 값이므로). 검증해야 할 것은 개별 표본이 아니라
        분포의 보정입니다: 평균이 0.5 근처이고, 양 꼬리가 각각 약 5% 여야 합니다.
        """
        values = np.array(
            [stats.probabilistic_sharpe_ratio(make_returns(0.0, 0.01, 3000, seed=s))
             for s in range(200)]
        )
        assert values.mean() == pytest.approx(0.5, abs=0.1)
        # 무의미한 전략이 유의하다고 판정되는 비율이 유의수준을 크게 넘으면 안 됩니다.
        assert (values > 0.95).mean() < 0.12, "위양성률이 과도합니다"
        assert (values < 0.05).mean() < 0.12

    def test_strong_positive_returns_near_one(self):
        r = make_returns(0.002, 0.01, 2000, seed=17)
        assert stats.probabilistic_sharpe_ratio(r) > 0.99

    def test_too_few_observations_is_nan(self):
        assert np.isnan(stats.probabilistic_sharpe_ratio(pd.Series([0.01, 0.02])))


class TestPbo:
    def test_pure_noise_trials_give_high_pbo(self):
        """전부 무의미한 전략이면 인샘플 최적 선택은 동전 던지기여야 합니다.

        PBO 가 0.5 근처면 '인샘플 1등이 아웃오브샘플에서 중앙값 미만일 확률이
        절반' -- 즉 선택에 정보가 없다는 뜻입니다.
        """
        rng = np.random.default_rng(21)
        idx = pd.bdate_range("2020-01-01", periods=1200)
        trials = pd.DataFrame(
            rng.normal(0, 0.01, size=(1200, 20)),
            index=idx,
            columns=[f"p{i}" for i in range(20)],
        )
        pbo = stats.probability_of_backtest_overfitting(trials, n_splits=8)
        assert 0.25 < pbo < 0.75, f"노이즈 시행의 PBO={pbo:.2f} (0.5 근처여야 함)"

    def test_genuinely_superior_strategy_gives_low_pbo(self):
        """한 전략이 진짜로 우월하면 인샘플 선택이 아웃오브샘플에서도 유지됩니다."""
        rng = np.random.default_rng(23)
        idx = pd.bdate_range("2020-01-01", periods=1200)
        trials = pd.DataFrame(
            rng.normal(0, 0.01, size=(1200, 10)),
            index=idx,
            columns=[f"p{i}" for i in range(10)],
        )
        trials["winner"] = rng.normal(0.0015, 0.01, 1200)  # 명백히 우월
        pbo = stats.probability_of_backtest_overfitting(trials, n_splits=8)
        assert pbo < 0.2, f"진짜 우월한 전략인데 PBO={pbo:.2f}"

    def test_requires_even_splits(self):
        df = pd.DataFrame(np.random.default_rng(1).normal(size=(200, 3)))
        with pytest.raises(ValueError, match="짝수"):
            stats.probability_of_backtest_overfitting(df, n_splits=7)

    def test_requires_multiple_trials(self):
        df = pd.DataFrame({"only": np.random.default_rng(1).normal(size=200)})
        with pytest.raises(ValueError, match="2개 이상"):
            stats.probability_of_backtest_overfitting(df)


class TestInformationCoefficient:
    def test_perfect_ranking_is_one(self):
        sig = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        fwd = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        assert stats.information_coefficient(sig, fwd) == pytest.approx(1.0)

    def test_inverted_ranking_is_minus_one(self):
        sig = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        fwd = pd.Series([0.05, 0.04, 0.03, 0.02, 0.01])
        assert stats.information_coefficient(sig, fwd) == pytest.approx(-1.0)

    def test_uses_rank_correlation_not_pearson(self):
        """극단값 하나가 상관계수를 지배하면 안 됩니다."""
        sig = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
        fwd = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        assert stats.information_coefficient(sig, fwd) == pytest.approx(1.0)


class TestSummarize:
    def test_reports_no_excess_when_there_is_none(self):
        """초과수익이 없으면 없다고 그대로 기록해야 합니다."""
        bench = make_returns(0.0008, 0.01, 1000, seed=31)
        strat = bench - 0.0002  # 벤치마크보다 확실히 나쁨
        out = stats.summarize(strat, benchmark=bench, n_trials=5)
        assert out["beats_benchmark"] is False
        assert out["excess"]["cagr"] < 0

    def test_records_trial_count(self):
        out = stats.summarize(make_returns(0.001, 0.01), n_trials=42)
        assert out["n_trials"] == 42
        assert "deflated_sharpe" in out
