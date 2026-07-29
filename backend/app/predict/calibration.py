"""확률 예측의 보정(calibration) 평가.

**이 모듈이 이 앱에서 가장 중요합니다.** 이유:

주식 수익률 예측에서 정확도(accuracy)는 거의 무의미한 지표입니다. 상승 확률이
52%인 시장에서 "항상 상승"이라고 답하면 정확도 52%가 나오지만 정보량은 0입니다.
중요한 것은 **"70% 확률이라고 말한 사건이 실제로 70% 빈도로 일어나는가"** 입니다.

문헌이 지적하는 핵심 실패 양상:
    확신-실력 격차(confidence-competence gap) -- 우연보다 약간 나은 수준인데
    일관되게 과신하는 모형은, 통상적인 베팅 규모 규칙 하에서 **장기 성장률이
    음수**가 됩니다. 즉 살짝 맞히는 것보다 확률을 정직하게 말하는 것이 중요합니다.

그래서 이 앱은 점 예측("내일 75,000원")을 하지 않고 **보정된 확률**을 제시하며,
그 보정 품질을 항상 함께 표시합니다.

Brier 점수 분해 (Murphy 분해):
    Brier = 신뢰도(reliability) - 분해능(resolution) + 불확실성(uncertainty)

    * reliability: 낮을수록 좋음. 예측 확률과 실제 빈도의 괴리 (보정 오차)
    * resolution: 높을수록 좋음. 기저율과 다른 예측을 하는 능력 (변별력)
    * uncertainty: 데이터 고유의 불확실성. 모형과 무관 -- 줄일 수 없습니다

    변별력 없이 보정만 잘하는 것도, 보정 없이 변별력만 있는 것도 쓸모가
    제한적입니다. 둘을 분리해서 봐야 하는 이유입니다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BrierDecomposition:
    brier: float
    reliability: float  # 낮을수록 좋음 (보정 오차)
    resolution: float  # 높을수록 좋음 (변별력)
    uncertainty: float  # 줄일 수 없는 부분
    base_rate: float
    n: int

    @property
    def skill_score(self) -> float:
        """기저율만 답하는 모형 대비 개선도 (0 = 무의미, 1 = 완벽).

        음수면 **기저율을 그냥 답하는 것보다 못하다**는 뜻입니다. 예측 모형에서
        드물지 않게 나오는 결과이며, 그때는 그렇게 보고해야 합니다.
        """
        if self.uncertainty <= 0:
            return 0.0
        return float((self.resolution - self.reliability) / self.uncertainty)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["skill_score"] = self.skill_score
        return d


def brier_decomposition(
    probabilities: pd.Series | np.ndarray,
    outcomes: pd.Series | np.ndarray,
    n_bins: int = 10,
) -> BrierDecomposition:
    """Brier 점수를 신뢰도·분해능·불확실성으로 분해.

    Args:
        probabilities: 예측 확률 (0~1)
        outcomes: 실제 결과 (0 또는 1)
        n_bins: 확률 구간 수
    """
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]

    n = len(p)
    if n == 0:
        return BrierDecomposition(np.nan, np.nan, np.nan, np.nan, np.nan, 0)
    if not ((p >= 0) & (p <= 1)).all():
        raise ValueError("확률은 0과 1 사이여야 합니다")
    if not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("결과는 0 또는 1 이어야 합니다")

    base_rate = float(y.mean())
    brier = float(np.mean((p - y) ** 2))
    uncertainty = base_rate * (1 - base_rate)

    # 구간별 집계
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)

    reliability = 0.0
    resolution = 0.0
    for b in range(n_bins):
        sel = bin_idx == b
        n_b = int(sel.sum())
        if n_b == 0:
            continue
        mean_p = float(p[sel].mean())
        mean_y = float(y[sel].mean())
        reliability += n_b * (mean_p - mean_y) ** 2
        resolution += n_b * (mean_y - base_rate) ** 2

    return BrierDecomposition(
        brier=brier,
        reliability=reliability / n,
        resolution=resolution / n,
        uncertainty=uncertainty,
        base_rate=base_rate,
        n=n,
    )


def reliability_curve(
    probabilities: pd.Series | np.ndarray,
    outcomes: pd.Series | np.ndarray,
    n_bins: int = 10,
) -> pd.DataFrame:
    """신뢰도 곡선 데이터 (프론트엔드 보정 그래프용).

    완벽히 보정된 모형은 predicted == observed 대각선 위에 놓입니다. 이 그래프를
    사용자에게 보여주는 것이 "정확도 57%" 같은 숫자보다 정직합니다 -- 어느 확률
    구간에서 모형이 과신하는지가 눈에 보이기 때문입니다.
    """
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    mask = np.isfinite(p) & np.isfinite(y)
    p, y = p[mask], y[mask]

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        sel = (p >= lo) & (p < hi) if b < n_bins - 1 else (p >= lo) & (p <= hi)
        n_b = int(sel.sum())
        rows.append(
            {
                "bin_lower": lo,
                "bin_upper": hi,
                "n": n_b,
                "predicted": float(p[sel].mean()) if n_b else np.nan,
                "observed": float(y[sel].mean()) if n_b else np.nan,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(
    probabilities: pd.Series | np.ndarray,
    outcomes: pd.Series | np.ndarray,
    n_bins: int = 10,
) -> float:
    """ECE: 구간별 |예측확률 - 실제빈도| 의 표본가중 평균.

    0에 가까울수록 보정이 좋습니다. 단일 숫자로 보정 품질을 요약할 때 씁니다.
    """
    curve = reliability_curve(probabilities, outcomes, n_bins)
    valid = curve.dropna(subset=["predicted", "observed"])
    if valid.empty or valid["n"].sum() == 0:
        return float("nan")
    weights = valid["n"] / valid["n"].sum()
    return float((weights * (valid["predicted"] - valid["observed"]).abs()).sum())


def out_of_sample_r2(
    predictions: pd.Series | np.ndarray,
    actuals: pd.Series | np.ndarray,
    benchmark: pd.Series | np.ndarray | float = 0.0,
) -> float:
    """아웃오브샘플 R² (Campbell-Thompson 방식).

    **분모의 기준선이 0(또는 무조건부 평균)입니다.** 인샘플 평균을 기준으로 쓰면
    미래 정보를 쓰는 것이 되어 R² 가 부풀려집니다.

    현실적 기대치 -- 이 숫자를 볼 때 반드시 참고하십시오:
        최첨단 ML 문헌(Gu, Kelly, Xiu 2020)의 개별종목 **월간** 아웃오브샘플
        R² 가 약 **0.33~0.40%** 입니다. 섹터·국가 단위는 0.29~0.95%.
        즉 R² 0.005 (0.5%) 면 문헌 최고 수준입니다. 백테스트에서 R² 0.1 (10%)
        같은 값이 나온다면 축하할 일이 아니라 **누수를 의심해야 할 신호**입니다.
    """
    pred = np.asarray(predictions, dtype=float)
    act = np.asarray(actuals, dtype=float)
    bench = (
        np.full_like(act, float(benchmark))
        if np.isscalar(benchmark)
        else np.asarray(benchmark, dtype=float)
    )

    mask = np.isfinite(pred) & np.isfinite(act) & np.isfinite(bench)
    pred, act, bench = pred[mask], act[mask], bench[mask]
    if len(act) == 0:
        return float("nan")

    ss_res = float(np.sum((act - pred) ** 2))
    ss_tot = float(np.sum((act - bench) ** 2))
    if ss_tot == 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


# 문헌이 보고한 현실적 상한. UI 와 리포트에서 사용자 기대를 조정하는 데 씁니다.
LITERATURE_BENCHMARKS = {
    "stock_monthly_r2_ml": 0.0040,  # Gu/Kelly/Xiu 신경망·트리 계열 상단
    "stock_monthly_r2_linear_penalized": 0.0026,
    "sector_monthly_r2_range": (0.0029, 0.0095),
    "note": (
        "정규화 없는 선형모형의 아웃오브샘플 R² 는 음수입니다(파국적 과적합). "
        "이 앱이 정규화를 강제하는 이유입니다."
    ),
}


def sanity_check_r2(r2: float, horizon_days: int) -> str | None:
    """R² 가 문헌 최고 수준을 크게 넘으면 경고 문구를 반환합니다.

    좋은 결과를 축하하는 대신 의심하기 위한 장치입니다. 수익률 예측에서
    비정상적으로 높은 R² 의 가장 흔한 원인은 뛰어난 모형이 아니라 **데이터
    누수**입니다.
    """
    if not np.isfinite(r2):
        return None
    # 월간(21영업일) 기준 문헌 상단을 기간에 맞춰 환산
    scale = max(horizon_days, 1) / 21.0
    ceiling = LITERATURE_BENCHMARKS["stock_monthly_r2_ml"] * scale
    if r2 > ceiling * 5:
        return (
            f"아웃오브샘플 R² {r2:.4f} 는 문헌 최고 수준(약 {ceiling:.4f})의 "
            f"5배를 넘습니다. 모형 성능이 아니라 **데이터 누수**를 먼저 "
            f"의심하십시오. 시점 이동, 미래 정보 포함, 생존편향을 점검하십시오."
        )
    return None
