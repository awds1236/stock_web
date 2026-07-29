"""성과 통계 -- 특히 과최적화 보정.

왜 샤프지수만으로는 안 되는가:
    Quantopian 888개 전략 분석에서 인샘플 샤프지수의 아웃오브샘플 예측력은
    R² < 0.025 였고, 백테스트를 많이 돌린 전략일수록 성과 괴리가 컸습니다.
    다중검정은 순전히 우연으로 '수익 나는' 전략을 발견하도록 수학적으로
    보장합니다. 그래서 시도 횟수를 세고 그만큼 깎아내는 보정이 필수입니다.

여기 구현한 보정:
    * Deflated Sharpe Ratio (DSR) -- 시도 횟수와 수익률 분포의 왜도·첨도를
      반영해 '이 샤프지수가 우연이 아닐 확률'을 계산.
    * Probability of Backtest Overfitting (PBO) -- 인샘플 최적 파라미터가
      아웃오브샘플에서 중앙값 미만으로 떨어질 확률.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats as sps

TRADING_DAYS = 252


@dataclass
class PerformanceStats:
    n_days: int
    total_return: float
    cagr: float
    ann_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    win_rate: float
    skew: float
    kurtosis: float
    avg_turnover: float

    def as_dict(self) -> dict:
        return asdict(self)


def compute_stats(returns: pd.Series, turnover: pd.Series | None = None) -> PerformanceStats:
    r = returns.dropna()
    n = len(r)
    if n == 0:
        return PerformanceStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    years = n / TRADING_DAYS
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 and equity.iloc[-1] > 0 else -1.0

    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if n > 1 else 0.0
    mean_ann = float(r.mean() * TRADING_DAYS)
    sharpe = mean_ann / vol if vol > 0 else 0.0

    downside = r[r < 0]
    dvol = float(downside.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = mean_ann / dvol if dvol > 0 else 0.0

    mdd = float((equity / equity.cummax() - 1).min())
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    # 수익률이 사실상 상수면 왜도·첨도는 수치적으로 정의되지 않습니다
    # (scipy 가 catastrophic cancellation 경고를 냅니다). 0 으로 보고합니다.
    degenerate = float(r.std(ddof=0)) < 1e-12
    skew_v = 0.0 if (degenerate or n <= 2) else float(sps.skew(r))
    kurt_v = 0.0 if (degenerate or n <= 3) else float(sps.kurtosis(r, fisher=True))

    return PerformanceStats(
        n_days=n,
        total_return=total,
        cagr=cagr,
        ann_volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=mdd,
        calmar=calmar,
        win_rate=float((r > 0).mean()),
        skew=skew_v,
        kurtosis=kurt_v,
        avg_turnover=float(turnover.mean()) if turnover is not None and len(turnover) else 0.0,
    )


def probabilistic_sharpe_ratio(
    returns: pd.Series, benchmark_sharpe: float = 0.0
) -> float:
    """PSR: 관측 샤프지수가 benchmark_sharpe 를 진짜로 초과할 확률.

    수익률의 왜도·첨도를 반영합니다. 정규분포를 가정한 표준 샤프 신뢰구간은
    금융 수익률의 두꺼운 꼬리를 무시해 유의성을 과대평가합니다.
    """
    r = returns.dropna()
    n = len(r)
    if n < 4:
        return float("nan")

    sr = float(r.mean() / r.std(ddof=1)) if r.std(ddof=1) > 0 else 0.0  # 일별 샤프
    sk = float(sps.skew(r))
    ku = float(sps.kurtosis(r, fisher=False))  # 비초과 첨도

    denom = np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr**2)
    if not np.isfinite(denom) or denom <= 0:
        return float("nan")

    bench_daily = benchmark_sharpe / np.sqrt(TRADING_DAYS)
    z = (sr - bench_daily) * np.sqrt(n - 1) / denom
    return float(sps.norm.cdf(z))


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int,
    trial_sharpe_variance: float | None = None,
) -> float:
    """DSR: 다중검정 보정 후 샤프지수가 진짜일 확률.

    Args:
        returns: 선택된(최적) 전략의 일별 수익률.
        n_trials: **실제로 시도한 전체 파라미터 조합 수**. 여기에 정직한 숫자를
            넣는 것이 이 함수의 전부입니다. 100개를 돌려보고 5개만 셌다면 보정은
            무의미합니다.
        trial_sharpe_variance: 시도된 전략들의 (연율) 샤프지수 분산. 미지정 시
            보수적으로 1.0 을 가정합니다.

    Returns:
        0~1 확률. 0.95 미만이면 '우연일 가능성을 배제 못함'으로 읽습니다.
    """
    if n_trials < 1:
        raise ValueError("n_trials 는 1 이상이어야 합니다")
    if n_trials == 1:
        return probabilistic_sharpe_ratio(returns, 0.0)

    var_sr = 1.0 if trial_sharpe_variance is None else float(trial_sharpe_variance)
    if var_sr <= 0:
        return probabilistic_sharpe_ratio(returns, 0.0)

    # 시도가 N번일 때 우연히 얻어지는 최대 샤프지수의 기댓값 (Bailey & López de Prado)
    euler = 0.5772156649015329
    e1 = sps.norm.ppf(1 - 1 / n_trials)
    e2 = sps.norm.ppf(1 - 1 / (n_trials * np.e))
    expected_max_sr = np.sqrt(var_sr) * ((1 - euler) * e1 + euler * e2)

    return probabilistic_sharpe_ratio(returns, float(expected_max_sr))


def probability_of_backtest_overfitting(
    trial_returns: pd.DataFrame, n_splits: int = 8
) -> float:
    """PBO: 인샘플 최적 전략이 아웃오브샘플에서 중앙값 미만일 확률 (CSCV).

    Args:
        trial_returns: columns = 각 파라미터 조합, index = 날짜, values = 일별 수익률.
        n_splits: 짝수. 시계열을 S개 블록으로 나눠 절반씩 IS/OOS 조합을 만듭니다.

    Returns:
        0~1. 0.5 이상이면 인샘플 최적 선택이 동전 던지기만도 못하다는 뜻입니다.
    """
    if n_splits % 2 != 0:
        raise ValueError("n_splits 는 짝수여야 합니다")
    df = trial_returns.dropna(how="all")
    if df.shape[1] < 2:
        raise ValueError("PBO 계산에는 2개 이상의 시도가 필요합니다")
    if len(df) < n_splits * 2:
        raise ValueError(f"관측치가 부족합니다: {len(df)} < {n_splits * 2}")

    blocks = np.array_split(np.arange(len(df)), n_splits)
    half = n_splits // 2
    logits: list[float] = []

    for is_idx in combinations(range(n_splits), half):
        oos_idx = [b for b in range(n_splits) if b not in is_idx]
        is_rows = np.concatenate([blocks[b] for b in is_idx])
        oos_rows = np.concatenate([blocks[b] for b in oos_idx])

        is_perf = _sharpe_by_column(df.iloc[is_rows])
        oos_perf = _sharpe_by_column(df.iloc[oos_rows])
        if is_perf.isna().all() or oos_perf.isna().all():
            continue

        best = is_perf.idxmax()
        # 인샘플 최적 전략의 아웃오브샘플 상대 순위 (1.0 = 최고, 0.0 = 최저)
        rank = oos_perf.rank(pct=True).get(best, np.nan)
        if not np.isfinite(rank):
            continue
        rank = min(max(float(rank), 1e-6), 1 - 1e-6)
        logits.append(np.log(rank / (1 - rank)))

    if not logits:
        return float("nan")
    # 로짓이 0 이하 = 아웃오브샘플 순위가 중앙값 이하 = 과최적화된 선택
    return float(np.mean(np.array(logits) <= 0))


def _sharpe_by_column(df: pd.DataFrame) -> pd.Series:
    mean = df.mean()
    sd = df.std(ddof=1)
    return (mean / sd.replace(0, np.nan)) * np.sqrt(TRADING_DAYS)


def information_coefficient(signal: pd.Series, forward_return: pd.Series) -> float:
    """Spearman 순위상관 (IC). 시그널의 횡단면 예측력.

    피어슨 대신 순위상관을 쓰는 이유는 소수 극단값이 상관계수를 지배하는 것을
    막기 위해서입니다.
    """
    paired = pd.concat([signal, forward_return], axis=1).dropna()
    if len(paired) < 3:
        return float("nan")
    rho, _ = sps.spearmanr(paired.iloc[:, 0], paired.iloc[:, 1])
    return float(rho)


def summarize(
    returns: pd.Series,
    *,
    benchmark: pd.Series | None = None,
    turnover: pd.Series | None = None,
    n_trials: int = 1,
) -> dict:
    """리포트용 성과 요약. 초과수익이 없으면 없다고 그대로 기록합니다."""
    out: dict = {"strategy": compute_stats(returns, turnover).as_dict()}
    out["psr"] = probabilistic_sharpe_ratio(returns)
    out["n_trials"] = n_trials
    out["deflated_sharpe"] = deflated_sharpe_ratio(returns, n_trials)

    if benchmark is not None:
        aligned = pd.concat([returns, benchmark], axis=1, join="inner").dropna()
        if not aligned.empty:
            excess = aligned.iloc[:, 0] - aligned.iloc[:, 1]
            out["benchmark"] = compute_stats(aligned.iloc[:, 1]).as_dict()
            out["excess"] = compute_stats(excess).as_dict()
            te = float(excess.std(ddof=1) * np.sqrt(TRADING_DAYS))
            out["information_ratio"] = (
                float(excess.mean() * TRADING_DAYS / te) if te > 0 else 0.0
            )
            out["beats_benchmark"] = bool(out["excess"]["cagr"] > 0)
    return out
