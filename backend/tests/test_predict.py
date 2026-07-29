"""예측 모듈 테스트.

가장 중요한 두 가지:
  1. 워크포워드에 **엠바고가 실제로 적용되는가** (라벨이 미래를 보므로
     학습·예측 구간이 겹치면 누수)
  2. 보정 지표가 **과신을 실제로 잡아내는가**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel

from app.predict import calibration as cal
from app.predict import features as feat
from app.predict import model as mdl
from app.predict import sectors as sec


# ── 보정 ─────────────────────────────────────────────────────────────────
class TestBrierDecomposition:
    def test_perfect_calibration_has_zero_reliability(self):
        """예측 확률 = 실제 빈도이면 신뢰도 오차가 0 이어야 합니다."""
        rng = np.random.default_rng(0)
        p = rng.choice([0.2, 0.5, 0.8], size=30_000)
        y = (rng.random(30_000) < p).astype(float)
        d = cal.brier_decomposition(p, y, n_bins=10)
        assert d.reliability < 0.001

    def test_overconfident_model_has_high_reliability_error(self):
        """확신-실력 격차를 잡아내야 합니다.

        항상 90% 확신하지만 실제로는 50%만 맞는 모형 -- 문헌이 지적하는
        가장 위험한 실패 양상입니다.
        """
        rng = np.random.default_rng(1)
        p = np.full(10_000, 0.9)
        y = (rng.random(10_000) < 0.5).astype(float)
        d = cal.brier_decomposition(p, y)
        assert d.reliability > 0.1, "과신이 감지되지 않았습니다"
        assert d.skill_score < 0, "기저율보다 못한데 skill_score 가 양수입니다"

    def test_base_rate_only_model_has_zero_resolution(self):
        """항상 기저율을 답하면 변별력이 0 입니다 (보정은 완벽하더라도)."""
        rng = np.random.default_rng(2)
        y = (rng.random(20_000) < 0.55).astype(float)
        p = np.full(20_000, y.mean())
        d = cal.brier_decomposition(p, y)
        assert d.resolution < 0.001
        assert d.reliability < 0.001
        assert abs(d.skill_score) < 0.01, "정보가 없는데 skill 이 있다고 나옵니다"

    def test_informative_model_has_positive_resolution(self):
        rng = np.random.default_rng(3)
        signal = rng.random(20_000)
        y = (rng.random(20_000) < signal).astype(float)
        d = cal.brier_decomposition(signal, y)
        assert d.resolution > 0.05
        assert d.skill_score > 0.3

    def test_decomposition_identity_holds(self):
        """Brier = reliability - resolution + uncertainty 항등식."""
        rng = np.random.default_rng(4)
        p = rng.random(5_000)
        y = (rng.random(5_000) < p).astype(float)
        d = cal.brier_decomposition(p, y, n_bins=20)
        assert d.brier == pytest.approx(
            d.reliability - d.resolution + d.uncertainty, abs=0.01
        )

    def test_rejects_invalid_inputs(self):
        with pytest.raises(ValueError, match="확률"):
            cal.brier_decomposition([1.5, 0.2], [1, 0])
        with pytest.raises(ValueError, match="결과"):
            cal.brier_decomposition([0.5, 0.2], [2, 0])


class TestCalibrationCurve:
    def test_well_calibrated_lies_on_diagonal(self):
        rng = np.random.default_rng(5)
        p = rng.random(40_000)
        y = (rng.random(40_000) < p).astype(float)
        curve = cal.reliability_curve(p, y, n_bins=10).dropna()
        assert (curve["predicted"] - curve["observed"]).abs().max() < 0.05

    def test_ece_detects_miscalibration(self):
        rng = np.random.default_rng(6)
        good_p = rng.random(20_000)
        good_y = (rng.random(20_000) < good_p).astype(float)
        bad_p = np.full(20_000, 0.9)
        bad_y = (rng.random(20_000) < 0.4).astype(float)
        assert cal.expected_calibration_error(good_p, good_y) < 0.05
        assert cal.expected_calibration_error(bad_p, bad_y) > 0.4


class TestOutOfSampleR2:
    def test_perfect_prediction_is_one(self):
        actual = pd.Series([0.01, -0.02, 0.03])
        assert cal.out_of_sample_r2(actual, actual) == pytest.approx(1.0)

    def test_zero_prediction_is_zero_against_zero_benchmark(self):
        actual = pd.Series([0.01, -0.02, 0.03])
        assert cal.out_of_sample_r2(pd.Series([0.0] * 3), actual) == pytest.approx(0.0)

    def test_worse_than_benchmark_is_negative(self):
        """음수 R² 가 나와야 하는 경우 -- 문헌에서 흔한 결과입니다."""
        actual = pd.Series([0.01, -0.02, 0.03])
        bad = pd.Series([0.5, 0.5, 0.5])
        assert cal.out_of_sample_r2(bad, actual) < 0

    def test_sanity_check_flags_implausible_r2(self):
        """문헌 최고 수준을 크게 넘으면 누수를 의심하라고 알려야 합니다."""
        warning = cal.sanity_check_r2(0.15, horizon_days=21)
        assert warning is not None and "누수" in warning

    def test_sanity_check_passes_realistic_r2(self):
        """문헌 수준(월간 0.4%)은 경고 대상이 아닙니다."""
        assert cal.sanity_check_r2(0.004, horizon_days=21) is None


# ── 특성·라벨 ────────────────────────────────────────────────────────────
class TestFeaturesAndLabels:
    @pytest.fixture
    def panel(self):
        p = make_panel(n_days=500, n_tickers=8, seed=42)
        p["shares"] = 1e6
        return p

    def test_features_are_created(self, panel):
        out = feat.build_features(panel)
        for col in feat.CORE_FEATURES:
            assert col in out.columns, f"{col} 이 생성되지 않았습니다"

    def test_features_use_no_future_information(self, panel):
        """특성을 자르고 계산해도 과거 구간 값이 같아야 합니다.

        미래 데이터가 과거 특성값에 영향을 주면 look-ahead 누수입니다.
        """
        full = feat.build_features(panel)
        cutoff = pd.Timestamp(panel["date"].unique()[300])
        truncated = feat.build_features(panel[pd.to_datetime(panel["date"]) <= cutoff])

        key = ["date", "ticker"]
        merged = full.merge(truncated, on=key, suffixes=("_full", "_trunc"))
        merged = merged[merged["date"] <= cutoff]

        for col in ["mom_12_1", "vol_20d", "rsi_14"]:
            a = merged[f"{col}_full"]
            b = merged[f"{col}_trunc"]
            both = a.notna() & b.notna()
            assert np.allclose(a[both], b[both], equal_nan=True), (
                f"{col} 이 미래 데이터에 의존합니다"
            )

    def test_labels_look_forward_not_backward(self, panel):
        """라벨은 t+1 진입 기준 미래 수익률이어야 합니다."""
        labeled = feat.make_labels(panel, horizon_days=5)
        one = labeled[labeled["ticker"] == "000000"].sort_values("date").reset_index(
            drop=True
        )
        # 수동 계산: t+1 시가 -> t+6 시가
        expected = one["open"].shift(-6) / one["open"].shift(-1) - 1
        pd.testing.assert_series_equal(
            one["fwd_return"], expected, check_names=False, rtol=1e-9
        )

    def test_last_rows_have_no_label(self, panel):
        """미래가 없는 구간에 라벨이 있으면 안 됩니다."""
        labeled = feat.make_labels(panel, horizon_days=21)
        one = labeled[labeled["ticker"] == "000000"].sort_values("date")
        assert one["fwd_return"].iloc[-21:].isna().all()

    def test_cross_sectional_rank_is_within_date_only(self):
        """순위는 같은 날짜 안에서만 -- 전체 기간 정규화는 미래 정보 누수."""
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 3),
                "ticker": ["A", "B", "C"] * 2,
                "x": [1.0, 2.0, 3.0, 300.0, 200.0, 100.0],
            }
        )
        ranked = feat.cross_sectional_rank(df, ["x"])
        day1 = ranked[ranked["date"] == "2024-01-01"]["x"].tolist()
        day2 = ranked[ranked["date"] == "2024-01-02"]["x"].tolist()
        # 2일차 값이 훨씬 큰데도 순위는 날짜 내부에서만 매겨집니다
        assert day1 == pytest.approx([1 / 3, 2 / 3, 1.0])
        assert day2 == pytest.approx([1.0, 2 / 3, 1 / 3])

    def test_feature_columns_differ_by_market(self):
        kr = feat.feature_columns("KR")
        us = feat.feature_columns("US")
        assert "flow_net_ratio_5d" in kr and "flow_net_ratio_5d" not in us
        assert "insider_cluster_90d" in us and "insider_cluster_90d" not in kr
        # 핵심 3축은 두 시장 공통 -- 수급/내부자는 보조입니다
        for col in feat.CORE_FEATURES:
            assert col in kr and col in us


# ── 워크포워드 ───────────────────────────────────────────────────────────
def make_learnable_panel(n_days=1400, n_tickers=12, seed=7, signal_strength=0.0):
    """특성이 미래 수익률을 signal_strength 만큼 설명하는 합성 패널."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    rows = []
    for i in range(n_tickers):
        x = rng.normal(size=n_days)
        noise = rng.normal(size=n_days)
        fwd = signal_strength * x + noise * 0.02
        rows.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "ticker": f"T{i:03d}",
                    "feat_a": x,
                    "feat_b": rng.normal(size=n_days),
                    "label": fwd,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


class TestWalkForward:
    def test_embargo_gap_exists_between_train_and_test(self):
        """학습 끝과 예측 시작 사이에 라벨 기간만큼 공백이 있어야 합니다.

        이 공백이 없으면 학습 라벨이 예측 구간과 겹칩니다 -- 시계열
        교차검증에서 가장 흔한 누수입니다.
        """
        df = make_learnable_panel()
        cfg = mdl.WalkForwardConfig(train_days=252, test_days=63, horizon_days=21)
        result = mdl.walk_forward_predict(df, ["feat_a", "feat_b"], "label", cfg)

        assert result.n_folds > 0
        for fold in result.folds:
            gap_days = (fold.test_start - fold.train_end).days
            assert gap_days > cfg.horizon_days, (
                f"엠바고 부족: 학습 종료 {fold.train_end.date()} → "
                f"예측 시작 {fold.test_start.date()} ({gap_days}일)"
            )

    def test_train_and_test_never_overlap(self):
        df = make_learnable_panel()
        result = mdl.walk_forward_predict(
            df, ["feat_a", "feat_b"], "label",
            mdl.WalkForwardConfig(train_days=252, test_days=63),
        )
        for fold in result.folds:
            assert fold.train_end < fold.test_start

    def test_pure_noise_gives_no_predictive_power(self):
        """예측 가능성이 없는 데이터에서 R² 가 0 이하여야 합니다.

        여기서 양의 R² 가 나오면 파이프라인에 누수가 있다는 뜻입니다.
        """
        df = make_learnable_panel(signal_strength=0.0, seed=11)
        result = mdl.walk_forward_predict(
            df, ["feat_a", "feat_b"], "label",
            mdl.WalkForwardConfig(train_days=504, test_days=126, model="ridge"),
        )
        r2 = cal.out_of_sample_r2(
            result.predictions["prediction"], result.predictions["actual"]
        )
        assert r2 < 0.01, f"노이즈 데이터에서 R²={r2:.4f} -- 누수 의심"

    def test_real_signal_is_detected(self):
        """대조군: 진짜 신호가 있으면 잡아내야 테스트가 무력하지 않습니다."""
        df = make_learnable_panel(signal_strength=0.02, seed=13)
        result = mdl.walk_forward_predict(
            df, ["feat_a", "feat_b"], "label",
            mdl.WalkForwardConfig(train_days=504, test_days=126, model="ridge"),
        )
        r2 = cal.out_of_sample_r2(
            result.predictions["prediction"], result.predictions["actual"]
        )
        assert r2 > 0.1, f"명백한 신호를 못 잡았습니다: R²={r2:.4f}"

    def test_predictions_are_all_out_of_sample(self):
        """예측 시점이 전부 첫 학습 구간 이후여야 합니다."""
        df = make_learnable_panel()
        cfg = mdl.WalkForwardConfig(train_days=252, test_days=63)
        result = mdl.walk_forward_predict(df, ["feat_a", "feat_b"], "label", cfg)
        first_train_end = result.folds[0].train_end
        assert result.predictions["date"].min() > first_train_end

    def test_classification_returns_probabilities(self):
        df = make_learnable_panel(signal_strength=0.02, seed=17)
        df["label"] = (df["label"] > 0).astype(float)
        result = mdl.walk_forward_predict(
            df, ["feat_a", "feat_b"], "label",
            mdl.WalkForwardConfig(
                train_days=504, test_days=126, model="logistic", task="classification"
            ),
        )
        p = result.predictions["prediction"]
        assert p.between(0, 1).all(), "확률이 0~1 범위를 벗어났습니다"

    def test_missing_columns_fail_loudly(self):
        df = make_learnable_panel()
        with pytest.raises(ValueError, match="필수 컬럼"):
            mdl.walk_forward_predict(df, ["nonexistent"], "label")

    def test_unregularized_model_is_not_offered(self):
        """정규화 없는 모형은 아웃오브샘플 R² 가 음수가 됩니다 (문헌)."""
        with pytest.raises(ValueError, match="알 수 없는 모형"):
            mdl.walk_forward_predict(
                make_learnable_panel(), ["feat_a"], "label",
                mdl.WalkForwardConfig(model="ols"),  # type: ignore[arg-type]
            )


# ── 섹터 ─────────────────────────────────────────────────────────────────
class TestSectors:
    @pytest.fixture
    def sector_panel(self):
        panel = make_panel(n_days=200, n_tickers=6, seed=21)
        mapping = pd.DataFrame(
            {
                "ticker": [f"{i:06d}" for i in range(6)],
                "sector": ["반도체"] * 3 + ["바이오"] * 3,
            }
        )
        return sec.attach_sector(panel, mapping)

    def test_aggregation_produces_one_row_per_sector_date(self, sector_panel):
        agg = sec.aggregate_to_sector(sector_panel)
        assert set(agg["sector"].unique()) == {"반도체", "바이오"}
        counts = agg.groupby("date").size()
        assert (counts <= 2).all()

    def test_equal_and_value_weighting_differ(self, sector_panel):
        eq = sec.aggregate_to_sector(sector_panel, weight="equal")
        vw = sec.aggregate_to_sector(sector_panel, weight="value")
        merged = eq.merge(vw, on=["date", "sector"], suffixes=("_eq", "_vw"))
        diff = (merged["ret_eq"] - merged["ret_vw"]).abs().sum()
        assert diff > 0, "동일가중과 시총가중이 동일합니다 -- 가중치 미적용 의심"

    def test_sector_index_is_chartable(self, sector_panel):
        """섹터에도 종목과 **같은 지표 코드**를 쓸 수 있어야 합니다."""
        idx = sec.sector_index(sec.aggregate_to_sector(sector_panel))
        assert {"date", "ticker", "open", "close"}.issubset(idx.columns)

        from app.indicators import price as px

        one = idx[idx["sector"] == "반도체"].sort_values("date")
        rsi = px.rsi(one["close"].reset_index(drop=True), 14)
        assert rsi.notna().any(), "섹터 지수에 지표를 적용할 수 없습니다"

    def test_breadth_is_a_fraction(self, sector_panel):
        b = sec.sector_breadth(sector_panel)
        assert b["advancing"].between(0, 1).all()

    def test_relative_strength_sums_to_about_zero_across_sectors(self, sector_panel):
        """모든 섹터의 시장 대비 초과수익 합은 0 근처여야 합니다."""
        rs = sec.relative_strength(sec.aggregate_to_sector(sector_panel))
        per_date = rs.groupby("date")["excess"].sum()
        assert per_date.abs().max() < 1e-9

    def test_missing_sector_column_fails_loudly(self):
        with pytest.raises(ValueError, match="sector"):
            sec.aggregate_to_sector(make_panel(n_days=10, n_tickers=2))
