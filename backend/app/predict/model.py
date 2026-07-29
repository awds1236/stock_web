"""워크포워드 예측 모형.

설계 원칙 (전부 문헌 근거가 있습니다):

  1. **정규화는 선택이 아니라 필수입니다.** Gu, Kelly, Xiu(2020)에서 정규화 없는
     선형모형의 아웃오브샘플 R² 는 **음수**였습니다(파국적 과적합). 이 모듈은
     정규화 없는 모형을 아예 제공하지 않습니다.

  2. **재학습은 워크포워드로만.** 전체 기간으로 한 번 학습하면 미래 정보가 새어
     들어갑니다. 학습 구간을 굴려가며 매번 다음 구간만 예측합니다.

  3. **엠바고(embargo).** 라벨이 h일 미래를 보므로, 학습 구간 끝과 예측 구간
     시작 사이에 최소 h일을 비워야 합니다. 이걸 빼먹으면 학습 라벨이 예측
     구간과 겹쳐 누수가 됩니다. 시계열 교차검증에서 가장 흔한 실수입니다.

  4. **기준선과 항상 비교.** 무조건부 평균, 모멘텀 단독 대비 개선이 없으면
     모형은 쓸모가 없습니다. 그 경우 그대로 보고합니다.

현실적 기대치:
    개별종목 월간 아웃오브샘플 R² 0.3~0.4% 가 문헌 최고 수준입니다. 그보다
    훨씬 높은 값이 나오면 누수를 의심하십시오(`calibration.sanity_check_r2`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge

ModelKind = Literal["ridge", "gbm", "logistic", "gbm_classifier"]
Task = Literal["regression", "classification"]


@dataclass
class WalkForwardConfig:
    train_days: int = 756  # 약 3년
    test_days: int = 63  # 약 3개월마다 재학습
    horizon_days: int = 21  # 라벨이 보는 미래 기간 (= 최소 엠바고)
    min_train_rows: int = 500
    model: ModelKind = "ridge"
    task: Task = "regression"
    # 정규화 강도. 교차검증 대상이지만 탐색 횟수를 DSR 에 반드시 포함시킬 것.
    alpha: float = 1.0
    random_state: int = 0

    @property
    def embargo_days(self) -> int:
        """학습 끝 ~ 예측 시작 사이에 비울 일수.

        라벨이 h일 미래를 보므로 최소 h일이 필요합니다. 여기에 하루를 더해
        경계에서의 부분 중첩까지 막습니다.
        """
        return self.horizon_days + 1


@dataclass
class FoldResult:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    n_train: int
    n_test: int
    predictions: pd.DataFrame  # date, ticker, prediction, actual


@dataclass
class WalkForwardResult:
    predictions: pd.DataFrame
    folds: list[FoldResult] = field(default_factory=list)
    config: WalkForwardConfig | None = None
    feature_names: list[str] = field(default_factory=list)

    @property
    def n_folds(self) -> int:
        return len(self.folds)


def _make_model(cfg: WalkForwardConfig):
    """정규화된 모형만 생성합니다."""
    if cfg.model == "ridge":
        return Ridge(alpha=cfg.alpha, random_state=None)
    if cfg.model == "gbm":
        return HistGradientBoostingRegressor(
            max_depth=3,  # 얕게 -- 깊은 트리는 노이즈를 외웁니다
            learning_rate=0.05,
            max_iter=200,
            l2_regularization=cfg.alpha,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=cfg.random_state,
        )
    if cfg.model == "logistic":
        # C = 1/alpha, 즉 alpha 가 클수록 강한 정규화
        return LogisticRegression(
            C=1.0 / max(cfg.alpha, 1e-6), max_iter=1000, random_state=cfg.random_state
        )
    if cfg.model == "gbm_classifier":
        return HistGradientBoostingClassifier(
            max_depth=3,
            learning_rate=0.05,
            max_iter=200,
            l2_regularization=cfg.alpha,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=cfg.random_state,
        )
    raise ValueError(f"알 수 없는 모형: {cfg.model}")


def walk_forward_predict(
    df: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    cfg: WalkForwardConfig | None = None,
) -> WalkForwardResult:
    """워크포워드 재학습 + 아웃오브샘플 예측.

    Args:
        df: date, ticker, 특성들, 라벨을 포함한 패널
        feature_cols: 사용할 특성 컬럼
        label_col: 예측 대상 (fwd_return / fwd_up / fwd_vol)

    Returns:
        WalkForwardResult -- predictions 는 **전부 아웃오브샘플**입니다.
    """
    cfg = cfg or WalkForwardConfig()
    missing = [c for c in [*feature_cols, label_col, "date", "ticker"] if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    data = df.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date")
    data = data.dropna(subset=[*feature_cols, label_col])
    if data.empty:
        return WalkForwardResult(_empty_predictions(), [], cfg, feature_cols)

    dates = np.sort(data["date"].unique())
    folds: list[FoldResult] = []
    chunks: list[pd.DataFrame] = []

    start = 0
    while True:
        train_lo = start
        train_hi = train_lo + cfg.train_days
        # 엠바고: 학습 끝과 예측 시작 사이를 비웁니다
        test_lo = train_hi + cfg.embargo_days
        test_hi = test_lo + cfg.test_days
        if test_lo >= len(dates):
            break
        test_hi = min(test_hi, len(dates))

        train_dates = dates[train_lo:train_hi]
        test_dates = dates[test_lo:test_hi]
        if len(train_dates) == 0 or len(test_dates) == 0:
            break

        train = data[data["date"].isin(train_dates)]
        test = data[data["date"].isin(test_dates)]
        if len(train) < cfg.min_train_rows or test.empty:
            start += cfg.test_days
            continue

        x_train = train[feature_cols].to_numpy(dtype=float)
        y_train = train[label_col].to_numpy(dtype=float)
        x_test = test[feature_cols].to_numpy(dtype=float)

        model = _make_model(cfg)
        if cfg.task == "classification":
            if len(np.unique(y_train)) < 2:
                start += cfg.test_days
                continue
            model.fit(x_train, y_train.astype(int))
            pred = model.predict_proba(x_test)[:, 1]
        else:
            model.fit(x_train, y_train)
            pred = model.predict(x_test)

        chunk = pd.DataFrame(
            {
                "date": test["date"].to_numpy(),
                "ticker": test["ticker"].to_numpy(),
                "prediction": pred,
                "actual": test[label_col].to_numpy(dtype=float),
            }
        )
        chunks.append(chunk)
        folds.append(
            FoldResult(
                train_start=pd.Timestamp(train_dates[0]),
                train_end=pd.Timestamp(train_dates[-1]),
                test_start=pd.Timestamp(test_dates[0]),
                test_end=pd.Timestamp(test_dates[-1]),
                n_train=len(train),
                n_test=len(test),
                predictions=chunk,
            )
        )
        start += cfg.test_days

    predictions = (
        pd.concat(chunks, ignore_index=True) if chunks else _empty_predictions()
    )
    return WalkForwardResult(predictions, folds, cfg, feature_cols)


def _empty_predictions() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "ticker", "prediction", "actual"])


# ── 기준선 ────────────────────────────────────────────────────────────────
def baseline_unconditional(df: pd.DataFrame, label_col: str) -> pd.Series:
    """무조건부 기준선: 항상 0 을 예측.

    수익률 예측에서 0(또는 무조건부 평균)은 이기기 매우 어려운 기준선입니다.
    이걸 못 이기면 모형은 쓸모가 없습니다.
    """
    return pd.Series(0.0, index=df.index)


def baseline_momentum(df: pd.DataFrame, momentum_col: str = "mom_12_1") -> pd.Series:
    """모멘텀 단독 기준선.

    복잡한 모형이 모멘텀 하나를 못 이긴다면 복잡도를 정당화할 수 없습니다.
    """
    if momentum_col not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return df[momentum_col]
