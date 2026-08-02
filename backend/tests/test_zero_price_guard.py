"""0원 가격 방어.

실제로 배포를 통째로 실패시킨 버그입니다. 한국 데이터가 들어온 첫 배포에서:

    ValueError: Input y contains infinity or a value too large for dtype('float64')

원인: KRX 는 거래정지·정리매매·상장 전 종목에 종가 **0** 을 내려줍니다.
`exit / entry - 1` 에서 entry 가 0이면 수익률이 무한대가 되고, `dropna` 는
inf 를 걸러내지 못해 그대로 sklearn 까지 갑니다. 미국(yfinance)은 0 을 주지
않아 이 경로가 몇 달간 드러나지 않았습니다.

여기서 고정하는 것:
    * 0원은 '가격 0원'이 아니라 **결측**입니다. 그 날의 수익률은 알 수 없습니다.
    * 라벨에도 특성에도 inf 가 남지 않습니다.
    * 그럼에도 inf 가 생기면 **학습 단계가 걸러냅니다** (앞으로 추가될 지표까지
      개별로 기억하지 않아도 되도록).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel

from app.predict import features as feat
from app.predict import model as mdl


def panel_with_zero_prices(n_days=900, n_tickers=8, seed=3, every=97):
    panel = make_panel(n_days=n_days, n_tickers=n_tickers, seed=seed)
    halted = sorted(panel["ticker"].unique())[0]
    mask = (panel["ticker"] == halted) & (panel.index % every == 0)
    panel.loc[mask, ["open", "high", "low", "close"]] = 0.0
    return panel, halted, int(mask.sum())


class TestLabels:
    def test_zero_entry_price_yields_missing_not_infinite(self):
        """0으로 나눈 수익률은 '무한대 수익'이 아니라 '알 수 없음'입니다."""
        panel = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "ticker": ["A", "A", "A"],
                "open": [100.0, 0.0, 120.0],
                "close": [100.0, 0.0, 120.0],
            }
        )
        out = feat.make_labels(panel, horizon_days=1)
        assert not np.isinf(out["fwd_return"].to_numpy(dtype=float)).any()
        # 진입가가 0인 날의 라벨은 결측이어야 합니다.
        assert pd.isna(out.loc[0, "fwd_return"])

    def test_no_infinities_survive_in_any_label(self):
        panel, _, injected = panel_with_zero_prices()
        assert injected > 0, "테스트가 실제로 0원을 주입했는지 확인"
        out = feat.make_labels(feat.build_features(panel), horizon_days=21)
        for col in ("fwd_return", "fwd_vol"):
            values = out[col].to_numpy(dtype=float)
            assert not np.isinf(values).any(), f"{col} 에 inf 가 남았습니다"

    def test_direction_label_is_not_fabricated_from_a_zero_price(self):
        """0원에서 오른 것처럼 보이는 가짜 '상승'을 만들면 안 됩니다."""
        panel = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "ticker": ["A", "A", "A"],
                "open": [100.0, 0.0, 120.0],
                "close": [100.0, 0.0, 120.0],
            }
        )
        out = feat.make_labels(panel, horizon_days=1)
        assert pd.isna(out.loc[0, "fwd_up"])


class TestFeatures:
    def test_no_infinities_in_core_features(self):
        """특성의 inf 는 라벨과 달리 조용히 순위를 왜곡합니다 -- 더 나쁩니다."""
        panel, _, _ = panel_with_zero_prices()
        out = feat.build_features(panel)
        for col in feat.CORE_FEATURES:
            if col not in out.columns:
                continue
            values = out[col].to_numpy(dtype=float)
            assert not np.isinf(values).any(), f"특성 {col} 에 inf 가 있습니다"


class TestEngineGuard:
    def test_walk_forward_survives_zero_prices(self):
        """이 크래시가 배포를 멈췄습니다. 다시는 멈추면 안 됩니다."""
        panel, _, _ = panel_with_zero_prices()
        df = feat.make_labels(feat.build_features(panel), horizon_days=21)
        cols = [c for c in feat.CORE_FEATURES if c in df.columns and df[c].notna().any()]
        ranked = feat.cross_sectional_rank(df, cols)
        cfg = mdl.WalkForwardConfig(
            train_days=504, test_days=126, horizon_days=21,
            model="ridge", task="regression",
        )
        result = mdl.walk_forward_predict(ranked, cols, "fwd_return", cfg)
        assert result.n_folds >= 1
        assert len(result.predictions) > 0

    def test_infinite_label_injected_downstream_is_dropped_not_fatal(self):
        """특성·라벨을 고쳐도, 앞으로 추가될 지표가 다시 inf 를 만들 수 있습니다.

        학습 직전에 한 번 더 막아 두면 그때마다 배포가 죽지 않습니다.
        """
        panel = make_panel(n_days=900, n_tickers=8, seed=5)
        df = feat.make_labels(feat.build_features(panel), horizon_days=21)
        cols = [c for c in feat.CORE_FEATURES if c in df.columns and df[c].notna().any()]
        ranked = feat.cross_sectional_rank(df, cols)

        # 파이프라인을 우회해 라벨에 직접 inf 를 주입합니다.
        victims = ranked.index[:: max(1, len(ranked) // 50)]
        ranked.loc[victims, "fwd_return"] = np.inf

        cfg = mdl.WalkForwardConfig(
            train_days=504, test_days=126, horizon_days=21,
            model="ridge", task="regression",
        )
        result = mdl.walk_forward_predict(ranked, cols, "fwd_return", cfg)
        assert result.n_folds >= 1, "inf 몇 개 때문에 전체가 죽으면 안 됩니다"
        assert np.isfinite(result.predictions["actual"].to_numpy(dtype=float)).all()

    def test_infinite_feature_is_dropped_too(self):
        panel = make_panel(n_days=900, n_tickers=8, seed=6)
        df = feat.make_labels(feat.build_features(panel), horizon_days=21)
        cols = [c for c in feat.CORE_FEATURES if c in df.columns and df[c].notna().any()]
        ranked = feat.cross_sectional_rank(df, cols)
        ranked.loc[ranked.index[::50], cols[0]] = -np.inf

        cfg = mdl.WalkForwardConfig(
            train_days=504, test_days=126, horizon_days=21,
            model="ridge", task="regression",
        )
        result = mdl.walk_forward_predict(ranked, cols, "fwd_return", cfg)
        assert result.n_folds >= 1


class TestHaltedStockIsNotMistakenForACrash:
    def test_a_halted_day_does_not_erase_the_rest_of_the_series(self):
        """거래정지 하루가 그 종목의 나머지 기간까지 버리면 안 됩니다."""
        panel, halted, _ = panel_with_zero_prices(n_days=400, n_tickers=4)
        out = feat.build_features(panel)
        rows = out[out["ticker"] == halted]
        assert rows["mom_1m"].notna().sum() > 100, "정상 구간까지 사라졌습니다"

    @pytest.mark.parametrize("bad", [0.0, -1.0, np.inf])
    def test_non_positive_and_infinite_prices_are_all_treated_as_missing(self, bad):
        panel = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "ticker": ["A", "A", "A"],
                "open": [100.0, bad, 120.0],
                "close": [100.0, bad, 120.0],
            }
        )
        out = feat.make_labels(panel, horizon_days=1)
        assert not np.isinf(out["fwd_return"].to_numpy(dtype=float)).any()
