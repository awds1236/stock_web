"""백테스트 엔진 건전성 테스트 -- 이 프로젝트의 관문.

여기가 통과하지 못하면 그 위에 쌓인 모든 수치는 의미가 없습니다. 가설 검증도,
대시보드도, 모의 포트폴리오도 전부 이 엔진의 출력을 신뢰한다는 전제 위에 있습니다.

검증하는 것:
  1. 완전예지 시그널이 이론적 최대 수익을 재현하는가
  2. **look-ahead 누수가 없는가** -- 가장 중요
  3. 랜덤 시그널이 정확히 비용만큼 잃는가 (숨은 편향 탐지)
  4. 비용이 회전율에 비례해 부과되는가
  5. 상장폐지가 손실로 인식되는가 (생존편향 탐지)
  6. 거래량 제약이 실제로 바인딩되는가
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import attach_signal, make_panel, open_to_open_returns

from app.backtest.engine import BacktestConfig, run_backtest


# ─────────────────────────────────────────────────────────────────────────
# 1. 완전예지: 엔진이 수익을 제대로 집계하는가
# ─────────────────────────────────────────────────────────────────────────
def test_perfect_foresight_captures_theoretical_max(panel, zero_cost):
    """signal[t] = ret_open[t+1] 이면 매일 최고 수익 종목을 잡아야 합니다.

    엔진은 T일 시그널로 T+1일 보유를 결정하므로, T+1일 수익률을 미리 아는
    시그널을 주면 매일 최대값을 취합해야 합니다. 이론값과 대조합니다.
    """
    rets = open_to_open_returns(panel)
    df = attach_signal(panel, rets.shift(-1), "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=1, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    # 이론값: 매일 횡단면 최대 수익률 (마지막 날은 청산 시점이 없어 제외)
    theoretical = rets.max(axis=1)
    actual = result.gross_returns

    common = actual.index.intersection(theoretical.dropna().index)
    # 첫날은 아직 보유 포지션이 없고, 마지막 날은 수익률이 정의되지 않습니다.
    common = common[1:-1]

    pd.testing.assert_series_equal(
        actual.loc[common], theoretical.loc[common], check_names=False, rtol=1e-9
    )


# ─────────────────────────────────────────────────────────────────────────
# 2. look-ahead 누수 탐지 -- 가장 중요한 테스트
# ─────────────────────────────────────────────────────────────────────────
def test_no_lookahead_same_day_signal_earns_nothing(panel, zero_cost):
    """당일 수익률을 시그널로 줘도 수익이 나면 안 됩니다.

    signal[t] = ret_open[t] 는 't일 시가→t+1일 시가 수익률'입니다. 엔진이 시점
    이동을 올바로 하면 이 시그널로는 t+1일에야 진입하므로 '어제 오른 종목을
    오늘 산다'는 모멘텀이 되고, 랜덤워크에서는 기댓값이 0입니다.

    반대로 엔진이 shift 를 빠뜨렸다면 당일 최고 수익 종목을 당일에 사는 셈이라
    막대한 수익이 나옵니다. 그 차이로 누수를 잡아냅니다.
    """
    rets = open_to_open_returns(panel)
    df = attach_signal(panel, rets, "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=1, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    r = result.gross_returns.iloc[1:-1]
    daily_vol = r.std(ddof=1)
    t_stat = r.mean() / (daily_vol / np.sqrt(len(r)))

    # 누수가 있으면 t 통계량이 수십~수백으로 치솟습니다.
    assert abs(t_stat) < 3.0, (
        f"look-ahead 누수 의심: 당일 시그널의 t={t_stat:.1f}. "
        "엔진이 시그널을 shift(1) 하고 있는지 확인하십시오."
    )


def test_lookahead_control_perfect_foresight_is_hugely_positive(panel, zero_cost):
    """대조군: 위 테스트가 '아무것도 감지 못하는 테스트'가 아님을 증명합니다.

    완전예지 시그널에서는 t 통계량이 반드시 크게 나와야 합니다. 그렇지 않다면
    테스트 자체가 무력한 것이므로, 2번 테스트의 통과도 의미가 없어집니다.
    """
    rets = open_to_open_returns(panel)
    df = attach_signal(panel, rets.shift(-1), "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=1, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    r = result.gross_returns.iloc[1:-1]
    t_stat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    assert t_stat > 10.0, f"완전예지인데 t={t_stat:.1f} -- 테스트가 무력합니다"


# ─────────────────────────────────────────────────────────────────────────
# 3. 랜덤 시그널: 숨은 편향 탐지
# ─────────────────────────────────────────────────────────────────────────
def test_random_signal_is_unbiased_without_costs(zero_cost):
    """무비용·랜덤 시그널의 기대수익은 0이어야 합니다.

    0이 아니라면 엔진 어딘가에 방향성 편향이 있다는 뜻입니다 -- 예를 들어 결측
    처리나 정렬 순서가 특정 종목군에 유리하게 작용하는 경우입니다.
    """
    panel = make_panel(n_days=600, n_tickers=40, seed=7)
    rng = np.random.default_rng(99)
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    noise = pd.DataFrame(
        rng.normal(size=opens.shape), index=opens.index, columns=opens.columns
    )
    df = attach_signal(panel, noise, "sig")

    cfg = BacktestConfig(
        hold_days=5, top_n=10, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    r = result.gross_returns.iloc[1:-1]
    t_stat = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    assert abs(t_stat) < 3.0, f"랜덤 시그널에 방향성 편향: t={t_stat:.2f}"


def test_random_signal_loses_exactly_the_cost_drag():
    """비용을 켜면 랜덤 시그널은 정확히 회전율 × 비용률 만큼 잃어야 합니다.

    비용이 과소 부과되면 모든 백테스트가 낙관적으로 나옵니다. 총비용이 실제
    매매량으로부터 계산한 이론값과 일치하는지 대조합니다.
    """
    from app.config import CostModel

    panel = make_panel(n_days=400, n_tickers=30, seed=11)
    rng = np.random.default_rng(5)
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    noise = pd.DataFrame(
        rng.normal(size=opens.shape), index=opens.index, columns=opens.columns
    )
    df = attach_signal(panel, noise, "sig")

    costs = CostModel(
        sell_tax=0.002, commission=0.0001, slippage=0.001, max_participation=1.0
    )
    cfg = BacktestConfig(
        hold_days=5, top_n=10, costs=costs, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    delta = result.weights.diff()
    delta.iloc[0] = result.weights.iloc[0]
    buys = delta.clip(lower=0).sum(axis=1)
    sells = (-delta.clip(upper=0)).sum(axis=1)
    expected = buys * (costs.commission + costs.slippage) + sells * (
        costs.commission + costs.slippage + costs.sell_tax
    )

    pd.testing.assert_series_equal(result.costs, expected, check_names=False, rtol=1e-12)
    assert result.costs.sum() > 0, "회전이 있는데 비용이 0입니다"
    # 순수익 = 총수익 - 비용 항등식
    pd.testing.assert_series_equal(
        result.daily_returns,
        result.gross_returns - result.costs,
        check_names=False,
        rtol=1e-12,
    )


# ─────────────────────────────────────────────────────────────────────────
# 4. 비용이 회전율에 반응하는가
# ─────────────────────────────────────────────────────────────────────────
def test_constant_selection_incurs_cost_only_at_entry():
    """선택이 매일 동일하면 초기 진입 이후 비용이 0에 수렴해야 합니다."""
    from app.config import CostModel

    panel = make_panel(n_days=200, n_tickers=20, seed=3)
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    # 종목 순서대로 고정 점수 -- 매일 같은 상위 5종목이 뽑힙니다.
    fixed = pd.DataFrame(
        np.tile(np.arange(opens.shape[1])[::-1], (opens.shape[0], 1)),
        index=opens.index,
        columns=opens.columns,
        dtype=float,
    )
    df = attach_signal(panel, fixed, "sig")

    costs = CostModel(sell_tax=0.002, commission=0.0001, slippage=0.001, max_participation=1.0)
    cfg = BacktestConfig(
        hold_days=3, top_n=5, costs=costs, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    # 초기 램프업(hold_days) 이후에는 매매가 없어야 합니다.
    steady = result.costs.iloc[cfg.hold_days + 2 :]
    assert steady.abs().max() < 1e-12, f"고정 선택인데 비용 발생: {steady.abs().max()}"


# ─────────────────────────────────────────────────────────────────────────
# 5. 생존편향 탐지
# ─────────────────────────────────────────────────────────────────────────
def test_delisting_is_recognized_as_total_loss(zero_cost):
    """상장폐지 종목은 조용히 사라지지 않고 -100% 로 인식되어야 합니다.

    상폐 종목을 데이터에서 빼면 수급 백테스트는 체계적으로 과대평가됩니다.
    기관이 담다가 상폐된 종목이 성과에서 빠지기 때문입니다.
    """
    panel = make_panel(n_days=100, n_tickers=10, seed=17)
    panel["is_delisted"] = 0
    doomed = "000003"
    delist_date = panel["date"].unique()[50]
    panel.loc[
        (panel["ticker"] == doomed) & (panel["date"] == delist_date), "is_delisted"
    ] = 1

    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    sig = pd.DataFrame(0.0, index=opens.index, columns=opens.columns)
    sig[doomed] = 1.0  # 항상 이 종목만 선택
    df = attach_signal(panel, sig, "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=1, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    # 상폐일 하루치 수익률이 -100% 로 잡혀야 합니다.
    loss_day = result.gross_returns.loc[pd.Timestamp(delist_date)]
    assert loss_day == pytest.approx(-1.0), f"상폐일 수익률이 {loss_day} (기대 -1.0)"


# ─────────────────────────────────────────────────────────────────────────
# 6. 거래량 제약
# ─────────────────────────────────────────────────────────────────────────
def test_volume_cap_binds_on_illiquid_names(zero_cost):
    """거래대금이 작으면 비중이 상한에 걸려야 합니다.

    이 제약이 없으면 비유동 소형주에서 체결 불가능한 수익률이 생성됩니다.
    수급 시그널의 초과수익이 비유동 구간에 몰린다는 연구가 있으므로, 바로 그
    구간에서 제약이 작동하는지 확인하는 것이 중요합니다.
    """
    from app.config import CostModel

    panel = make_panel(n_days=120, n_tickers=10, seed=23, trading_value=1e12)
    illiquid = "000005"
    # 자본 1억, 참여율 1% → 거래대금 1억이면 상한 비중은 1%
    panel.loc[panel["ticker"] == illiquid, "value"] = 1e8

    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    sig = pd.DataFrame(0.0, index=opens.index, columns=opens.columns)
    sig[illiquid] = 1.0
    df = attach_signal(panel, sig, "sig")

    costs = CostModel(
        sell_tax=0.0, commission=0.0, slippage=0.0, max_participation=0.01
    )
    cfg = BacktestConfig(
        hold_days=1,
        top_n=1,
        costs=costs,
        apply_volume_cap=True,
        initial_capital=100_000_000.0,
    )
    result = run_backtest(df, "sig", cfg)

    held = result.weights[illiquid].iloc[2:-1]
    assert held.max() < 0.999, "거래량 제약이 전혀 작동하지 않았습니다"
    # 상한 = 1e8 * 0.01 / 1e8 = 0.01
    assert held.max() == pytest.approx(0.01, rel=0.05), f"상한 비중 {held.max()}"

    # 대조군: 제약을 끄면 비중이 100% 여야 합니다.
    cfg_off = BacktestConfig(
        hold_days=1, top_n=1, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    uncapped = run_backtest(df, "sig", cfg_off)
    assert uncapped.weights[illiquid].iloc[2:-1].max() == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────
# 7. 계약 검증
# ─────────────────────────────────────────────────────────────────────────
def test_weights_never_exceed_full_investment(panel, zero_cost):
    """비중 합계가 1을 넘으면 안 됩니다 (레버리지 없음)."""
    rng = np.random.default_rng(31)
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    noise = pd.DataFrame(
        rng.normal(size=opens.shape), index=opens.index, columns=opens.columns
    )
    df = attach_signal(panel, noise, "sig")

    cfg = BacktestConfig(
        hold_days=10, top_n=5, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)
    assert result.weights.sum(axis=1).max() <= 1.0 + 1e-9


def test_missing_signal_names_are_not_selected(panel, zero_cost):
    """시그널이 결측인 기간에는 그 종목이 선택되면 안 됩니다.

    결측을 0으로 채우면 '시그널 없음'이 '시그널 0'으로 둔갑해 순위에 끼어듭니다.
    상장 초기 종목이나 수급 데이터가 아직 없는 종목이 대거 선택되는 흔한 버그입니다.

    실제 위험은 '전 구간 결측'이 아니라 **부분 결측**입니다. 전 구간 결측이면
    피벗 단계에서 컬럼째 사라져 저절로 걸러지지만, 부분 결측은 살아남아
    순위 계산에 끼어들 수 있습니다. 그래서 앞 절반만 결측인 종목으로 검증합니다.
    """
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    sig = pd.DataFrame(-1.0, index=opens.index, columns=opens.columns)
    ghost = "000007"
    split = len(opens) // 2
    # 앞 절반은 결측, 뒷 절반은 압도적 1위 점수
    sig.iloc[:split, sig.columns.get_loc(ghost)] = np.nan
    sig.iloc[split:, sig.columns.get_loc(ghost)] = 100.0
    df = attach_signal(panel, sig, "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=3, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)

    held = result.weights[ghost]
    # 결측 구간에서는 보유 0 (시그널 t → 보유 t+1 이므로 경계 하루 여유)
    assert held.iloc[: split - 1].abs().max() == 0.0, "결측 구간인데 선택되었습니다"
    # 유효 구간에서는 최상위 점수이므로 반드시 보유
    assert held.iloc[split + 1 :].max() > 0.0, "유효 시그널인데 선택되지 않았습니다"


def test_all_nan_signal_column_is_dropped(panel, zero_cost):
    """전 구간 시그널이 없는 종목은 아예 보유 대상에 오르지 않아야 합니다."""
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    sig = pd.DataFrame(-1.0, index=opens.index, columns=opens.columns)
    ghost = "000007"
    sig[ghost] = np.nan
    df = attach_signal(panel, sig, "sig")

    cfg = BacktestConfig(
        hold_days=1, top_n=3, costs=zero_cost, apply_volume_cap=False, initial_capital=1.0
    )
    result = run_backtest(df, "sig", cfg)
    held = result.weights.get(ghost)
    assert held is None or held.abs().max() == 0.0


def test_rejects_invalid_config(panel):
    df = panel.assign(sig=1.0)
    with pytest.raises(ValueError, match="hold_days"):
        run_backtest(df, "sig", BacktestConfig(hold_days=0))
    with pytest.raises(ValueError, match="top_n"):
        run_backtest(df, "sig", BacktestConfig(top_n=0))
    with pytest.raises(ValueError, match="필수 컬럼"):
        run_backtest(panel, "does_not_exist", BacktestConfig())
