"""횡단면 수급 전략 백테스트 엔진.

이 엔진이 지키는 규칙 -- 하나라도 어기면 결과 전체가 거짓말이 됩니다:

  1. **T+1 시가 체결.** T일 수급 확정치(18:00 이후)로 만든 시그널은 T+1 시가에만
     실행할 수 있습니다. 종가 체결은 이 앱에서 지원하지 않습니다 -- 지원하면
     누군가 반드시 씁니다.
  2. **비용 반영.** 매수/매도 수수료, 매도 시 거래세, 슬리피지를 전부 부과합니다.
     세율은 설정에서 주입되며 결과 리포트에 함께 기록됩니다.
  3. **거래량 제약.** 종목당 비중은 그날 거래대금의 일정 비율 이내로 제한됩니다.
     이 제약이 없으면 소형주에서 체결 불가능한 수익률이 나옵니다.
  4. **생존편향 제거.** 상장폐지 종목은 상폐일까지 유니버스에 남고 상폐 시 손실
     처리됩니다.

수익률 정의:
    ret_open[d] = open[d+1] / open[d] - 1
    즉 d일 시가에 진입해 d+1일 시가에 청산했을 때의 수익률입니다. 시그널로부터의
    시점 이동은 전부 이 엔진 안에서 일어나며, 지표 계산 함수는 시점 이동을 하지
    않습니다(app/indicators/flow.py 참조).

보유 방식 -- 중첩 포트폴리오:
    매일 상위 N종목을 뽑아 hold_days 일 보유합니다. 서로 다른 날의 진입이 겹치므로
    임의 시점의 포트폴리오는 최근 hold_days 일치 선택의 평균이 됩니다. 자본을
    hold_days 등분해 매일 1/hold_days 씩 굴리는 것과 같습니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from app.config import CostModel, cost_model


@dataclass
class BacktestConfig:
    hold_days: int = 20
    top_n: int = 20
    direction: Literal["long", "short"] = "long"
    initial_capital: float = 100_000_000.0  # 1억원
    costs: CostModel = field(default_factory=lambda: cost_model)
    apply_volume_cap: bool = True
    min_trading_value: float = 0.0  # 유니버스 필터: 최소 일 거래대금


@dataclass
class BacktestResult:
    daily_returns: pd.Series  # 비용 차감 후 일별 수익률
    gross_returns: pd.Series  # 비용 차감 전
    costs: pd.Series  # 일별 비용 (수익률 단위)
    weights: pd.DataFrame  # date × ticker 보유 비중
    equity: pd.Series  # 누적 자산 곡선
    turnover: pd.Series  # 일별 단방향 회전율
    config: BacktestConfig
    meta: dict = field(default_factory=dict)

    @property
    def total_return(self) -> float:
        return float(self.equity.iloc[-1] / self.equity.iloc[0] - 1) if len(self.equity) else 0.0


REQUIRED_COLUMNS = ("date", "ticker", "open")


def _pivot(panel: pd.DataFrame, column: str) -> pd.DataFrame:
    return panel.pivot_table(index="date", columns="ticker", values=column, aggfunc="last")


def _select_top_n(
    scores: pd.DataFrame, top_n: int, direction: str, eligible: pd.DataFrame | None
) -> pd.DataFrame:
    """일자별 상위 N종목에 1/N 비중을 배정한 지시행렬.

    시그널이 결측이거나 유니버스 조건을 못 맞춘 종목은 애초에 순위에서 제외합니다.
    (결측을 0으로 채우면 '시그널 없음'이 '시그널 0'으로 둔갑해 순위에 끼어듭니다.)
    """
    masked = scores if eligible is None else scores.where(eligible)
    ascending = direction == "short"
    ranks = masked.rank(axis=1, ascending=ascending, method="first", na_option="keep")
    selected = (ranks <= top_n) & ranks.notna()

    counts = selected.sum(axis=1).replace(0, np.nan)
    return selected.astype(float).div(counts, axis=0).fillna(0.0)


def run_backtest(
    panel: pd.DataFrame,
    signal_col: str,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """수급 시그널 횡단면 백테스트.

    Args:
        panel: columns = [date, ticker, open, (close), (value), (is_delisted), signal_col]
            `value` 는 일 거래대금(원). 거래량 제약과 유니버스 필터에 사용됩니다.
        signal_col: 점수 컬럼. 클수록 매수 우선(direction='long' 기준).
        config: 백테스트 설정.

    Returns:
        BacktestResult
    """
    cfg = config or BacktestConfig()
    missing = [c for c in (*REQUIRED_COLUMNS, signal_col) if c not in panel.columns]
    if missing:
        raise ValueError(f"panel 에 필수 컬럼이 없습니다: {missing}")
    if cfg.hold_days < 1:
        raise ValueError("hold_days 는 1 이상이어야 합니다")
    if cfg.top_n < 1:
        raise ValueError("top_n 은 1 이상이어야 합니다")

    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "ticker"])

    opens = _pivot(df, "open")
    scores = _pivot(df, signal_col)
    values = _pivot(df, "value") if "value" in df.columns else None

    # 시가 → 다음날 시가 수익률. 마지막 날은 청산 시점이 없으므로 NaN.
    ret_open = opens.shift(-1) / opens - 1

    # 상장폐지 처리: 상폐 다음 거래일 시가가 없으면 수익률이 NaN 이 되어 조용히
    # 사라집니다(= 생존편향). 명시적으로 -100% 로 처리해 손실을 인식시킵니다.
    if "is_delisted" in df.columns:
        delisted = _pivot(df, "is_delisted").fillna(0).astype(bool)
        ret_open = ret_open.mask(delisted, -1.0)

    # 유니버스 적격성: 시가·수익률이 존재하고 최소 거래대금을 넘는 종목만
    eligible = opens.notna() & ret_open.notna()
    if values is not None and cfg.min_trading_value > 0:
        eligible &= values >= cfg.min_trading_value

    target = _select_top_n(scores, cfg.top_n, cfg.direction, eligible)

    # ── 시점 이동: 여기가 이 엔진의 핵심 한 줄 ────────────────────────────
    # T일 시그널로 뽑은 종목은 T+1 시가부터 보유합니다. shift(1) 이 그 지연이고,
    # rolling(hold_days).mean() 이 중첩 보유입니다.
    held = target.shift(1).rolling(cfg.hold_days, min_periods=1).mean().fillna(0.0)

    if cfg.apply_volume_cap and values is not None:
        held = _apply_volume_cap(held, values, ret_open, cfg)

    gross = (held * ret_open.fillna(0.0)).sum(axis=1)

    # 비용: 비중 변화량 기준. 매수분과 매도분에 서로 다른 비용률 적용.
    delta = held.diff()
    delta.iloc[0] = held.iloc[0]
    buys = delta.clip(lower=0).sum(axis=1)
    sells = (-delta.clip(upper=0)).sum(axis=1)

    c = cfg.costs
    buy_rate = c.commission + c.slippage
    sell_rate = c.commission + c.slippage + c.sell_tax
    cost_series = buys * buy_rate + sells * sell_rate

    net = gross - cost_series
    equity = cfg.initial_capital * (1 + net).cumprod()

    return BacktestResult(
        daily_returns=net,
        gross_returns=gross,
        costs=cost_series,
        weights=held,
        equity=equity,
        turnover=buys + sells,
        config=cfg,
        meta={
            "signal": signal_col,
            "start": str(net.index.min().date()) if len(net) else None,
            "end": str(net.index.max().date()) if len(net) else None,
            "n_days": int(len(net)),
            # 세율을 결과에 박아둡니다. 나중에 리포트만 보고도 어떤 비용 가정이었는지
            # 알 수 있어야 합니다.
            "cost_model": {
                "sell_tax": c.sell_tax,
                "commission": c.commission,
                "slippage": c.slippage,
                "max_participation": c.max_participation,
            },
        },
    )


def _apply_volume_cap(
    held: pd.DataFrame,
    values: pd.DataFrame,
    ret_open: pd.DataFrame,
    cfg: BacktestConfig,
) -> pd.DataFrame:
    """종목별 비중을 그날 거래대금의 일정 비율 이내로 제한.

    상한을 넘는 부분은 다른 종목으로 재배분하지 않고 **현금으로 남깁니다**. 재배분은
    '못 산 만큼 다른 걸 더 산다'는 낙관적 가정인데, 실제로는 상한에 걸리는 상황
    자체가 유동성 부족을 뜻하므로 현금 보유가 보수적이고 정직합니다.

    자본은 전일까지의 성과로 성장하므로, 상한 계산에는 **전일 종료 시점 자산**을
    씁니다(당일 자산을 쓰면 순환 참조).
    """
    capped = held.copy()
    equity = cfg.initial_capital
    vals = values.reindex_like(held)
    rets = ret_open.reindex_like(held).fillna(0.0)
    cap_ratio = cfg.costs.max_participation

    for i in range(len(held.index)):
        row = held.iloc[i]
        if row.abs().sum() > 0:
            max_notional = vals.iloc[i] * cap_ratio
            max_weight = (max_notional / equity).replace([np.inf, -np.inf], np.nan)
            # 거래대금 정보가 없는 종목은 제한하지 않습니다(정보 부재를 제약으로
            # 오해하지 않기 위함). ingest 단계에서 결측을 줄이는 것이 옳은 해법입니다.
            row = row.clip(upper=max_weight.fillna(np.inf))
            capped.iloc[i] = row
        equity *= 1 + float((capped.iloc[i] * rets.iloc[i]).sum())
        if equity <= 0:
            equity = 1e-9
            break

    return capped


def benchmark_returns(panel: pd.DataFrame, weighting: str = "equal") -> pd.Series:
    """벤치마크: 유니버스 동일가중 바이앤홀드 (시가→시가 기준, 무비용).

    전략 수익률과 **같은 수익률 정의**를 써야 비교가 성립합니다. 종가 기준
    벤치마크와 시가 기준 전략을 비교하면 하루치 갭이 통째로 성과 차이로 둔갑합니다.
    """
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    opens = _pivot(df, "open")
    ret = opens.shift(-1) / opens - 1
    if weighting == "value" and "market_cap" in df.columns:
        caps = _pivot(df, "market_cap").where(ret.notna())
        w = caps.div(caps.sum(axis=1), axis=0)
        return (w * ret).sum(axis=1)
    return ret.mean(axis=1, skipna=True).fillna(0.0)
