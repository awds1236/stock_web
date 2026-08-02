"""예측 특성(feature) 생성.

특성 선택의 근거:
    Gu, Kelly, Xiu(2020)는 900개 이상의 후보 예측변수로 여러 ML 모형을 비교했는데,
    방법론과 무관하게 **모멘텀·유동성·변동성**이 일관되게 최상위 예측변수로
    지목되었습니다. 그래서 이 모듈은 그 세 축을 핵심으로 삼고, 나머지는 보조로
    둡니다. 변수를 무작정 늘리는 것은 다중검정 부담만 키웁니다.

    한국의 수급(연기금 등)과 미국의 내부자 매매는 **보조 특성**입니다. 메인이
    아닙니다 -- 한국 연구에서 국내 기관 지분 변화의 수익률 영향이 유의하지 않다는
    결과가 있고, 내부자 매수의 초과수익도 거래규모 제약 하에서 사라질 수 있다는
    반론이 있기 때문입니다.

시점 규칙:
    모든 특성은 **t 시점까지의 정보만으로** 계산됩니다. 미래로의 시점 이동은
    라벨 생성(`make_labels`)과 백테스트 엔진이 담당합니다. 이 분업을 어기면
    look-ahead 누수가 생기고, 그 증상은 비정상적으로 높은 R² 입니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators import price as px

# 핵심 3축 + 보조. 이름 순서가 곧 중요도 서술이며 문서·UI 에서 그대로 씁니다.
CORE_FEATURES = [
    # 모멘텀
    "mom_12_1",
    "mom_1m",
    "pct_from_52w_high",
    # 유동성
    "turnover_20d",
    "amihud_20d",
    "log_dollar_volume",
    # 변동성
    "vol_20d",
    "vol_60d",
    "vol_ratio",
]

AUX_FEATURES = [
    "rsi_14",
    "macd_hist_norm",
    "bollinger_pct_b",
    "log_market_cap",
]

FLOW_FEATURES = ["flow_net_ratio_5d", "flow_persistence_20d"]  # 한국 전용
INSIDER_FEATURES = ["insider_cluster_90d", "insider_buy_to_cap"]  # 미국 전용


def build_features(
    panel: pd.DataFrame,
    *,
    include_aux: bool = True,
) -> pd.DataFrame:
    """종목 패널에서 예측 특성을 생성.

    Args:
        panel: columns = [date, ticker, open, high, low, close, volume, value,
                          (market_cap), (shares)]

    Returns:
        입력 패널 + 특성 컬럼. 워밍업 구간은 NaN 이며 제거하지 않습니다 --
        제거 시점을 모형 학습 단계가 결정해야 하기 때문입니다.
    """
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    # 섹터 지수처럼 거래량·시총이 없는 패널도 지나갑니다. 없는 컬럼은 NaN 으로
    # 두어 '값이 없음'을 보존합니다 -- 0 으로 채우면 '거래가 전혀 없었다'는
    # 뜻이 되어 유동성 순위가 뒤집힙니다.
    for col in ("volume", "value", "market_cap", "shares", "high", "low"):
        if col not in df.columns:
            df[col] = np.nan

    # 라벨과 같은 이유로 여기서도 0원을 결측 처리합니다. 특성에 들어간 inf 는
    # 라벨만큼 요란하게 죽지 않고 **조용히 순위를 왜곡**하기 때문에 더 나쁩니다.
    df["close"] = _positive(df["close"])

    g = df.groupby("ticker", sort=False, group_keys=False)

    # ── 모멘텀 ──────────────────────────────────────────────────────────
    df["mom_12_1"] = g["close"].transform(lambda s: px.momentum_12_1(s))
    df["mom_1m"] = g["close"].transform(lambda s: s / s.shift(21) - 1)
    df["pct_from_52w_high"] = g["close"].transform(lambda s: px.pct_from_52w_high(s))

    # ── 유동성 ──────────────────────────────────────────────────────────
    # 회전율: 상장주식수가 있으면 거래량/주식수, 없으면 거래대금/시총으로 대체.
    raw_turnover = df["volume"] / df["shares"].replace(0, np.nan)
    fallback = df["value"] / df["market_cap"].replace(0, np.nan)
    df["_turnover"] = raw_turnover.where(raw_turnover.notna(), fallback)
    df["turnover_20d"] = df.groupby("ticker", sort=False)["_turnover"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )

    df["_ret"] = df.groupby("ticker", sort=False)["close"].transform(
        lambda s: s.pct_change()
    )
    df["_amihud_daily"] = df["_ret"].abs() / df["value"].replace(0, np.nan)
    df["amihud_20d"] = df.groupby("ticker", sort=False)["_amihud_daily"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    df["log_dollar_volume"] = np.log1p(df["value"].clip(lower=0))

    # ── 변동성 ──────────────────────────────────────────────────────────
    df["vol_20d"] = g["close"].transform(lambda s: px.realized_volatility(s, 20))
    df["vol_60d"] = g["close"].transform(lambda s: px.realized_volatility(s, 60))
    # 단기/장기 변동성 비율 -- 변동성 국면 전환의 대용치
    df["vol_ratio"] = df["vol_20d"] / df["vol_60d"].replace(0, np.nan)

    if include_aux:
        df["rsi_14"] = df.groupby("ticker", sort=False)["close"].transform(
            lambda s: px.rsi(s, 14)
        )
        # MACD 히스토그램은 가격 수준에 비례하므로 정규화해야 종목 간 비교가
        # 가능합니다. 원값을 그대로 쓰면 고가 종목이 항상 큰 값을 갖습니다.
        df["_macd_hist"] = df.groupby("ticker", sort=False)["close"].transform(
            lambda s: px.macd(s)["hist"]
        )
        df["macd_hist_norm"] = df["_macd_hist"] / df["close"].replace(0, np.nan)
        df["bollinger_pct_b"] = df.groupby("ticker", sort=False)["close"].transform(
            lambda s: px.bollinger(s)["pct_b"]
        )
        df["log_market_cap"] = np.log1p(df["market_cap"].clip(lower=0))

    return df.drop(
        columns=[c for c in ("_ret", "_turnover", "_amihud_daily", "_macd_hist")
                 if c in df.columns]
    )


def make_labels(
    panel: pd.DataFrame,
    horizon_days: int = 21,
    *,
    price_col: str = "open",
) -> pd.DataFrame:
    """예측 대상(라벨) 생성.

    **시점 이동이 전부 여기서 일어납니다.** 특성은 t 시점까지의 정보이고,
    라벨은 t+1 부터 t+1+horizon 까지의 수익률입니다. 진입이 T+1 시가라는
    백테스트 엔진의 가정과 일치시킵니다.

    Returns:
        + fwd_return: 미래 수익률 (회귀 대상)
        + fwd_up: 상승 여부 0/1 (확률 예측 대상)
        + fwd_vol: 미래 실현변동성 (수익률보다 예측 가능성이 높은 대상)
    """
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    # **0원은 가격이 아니라 '거래 없음'입니다** (거래정지·정리매매·상장 전).
    #
    # KRX 는 그런 날 0 을 내려줍니다. 그대로 나누면 수익률이 무한대가 되고,
    # `dropna` 는 inf 를 걸러내지 못해 학습 단계에서 통째로 터집니다 -- 실제로
    # 한국 데이터가 들어오자 배포가 이 예외로 실패했습니다:
    #
    #     ValueError: Input y contains infinity or a value too large for float64
    #
    # 미국(yfinance)은 0 을 주지 않아 이 경로가 드러나지 않았습니다.
    df["_px"] = _positive(df[price_col])
    df["_cl"] = _positive(df["close"])
    g = df.groupby("ticker", sort=False, group_keys=False)

    entry = g["_px"].transform(lambda s: s.shift(-1))
    exit_ = g["_px"].transform(lambda s: s.shift(-1 - horizon_days))
    df["fwd_return"] = exit_ / entry - 1
    df["fwd_up"] = (df["fwd_return"] > 0).astype(float).where(df["fwd_return"].notna())

    # 미래 실현변동성. 문헌상 수익률 방향보다 훨씬 예측 가능한 대상이므로
    # 이 앱은 이것을 1급 예측 대상으로 다룹니다.
    df["_dr"] = g["_cl"].transform(lambda s: s.pct_change())
    # `groupby.apply` 가 아니라 `transform` 을 쓰는 이유: 그룹이 하나뿐일 때
    # apply 는 Series 가 아니라 DataFrame 을 돌려줘 대입이 터집니다
    # ("Cannot set a DataFrame with multiple columns to the single column").
    # 종목이 하나인 패널은 드물지만, 그때만 죽는 코드는 찾기 어렵습니다.
    df["fwd_vol"] = g["_dr"].transform(
        lambda s: s.shift(-1).rolling(horizon_days).std().shift(-horizon_days + 1)
    ) * np.sqrt(252)
    # 위 마스킹으로 대부분 막히지만, 라벨에 inf 를 절대 남기지 않는 것을
    # 마지막으로 한 번 더 보장합니다. 라벨의 inf 는 조용히 넘어가지 않고
    # 학습을 죽입니다.
    for col in ("fwd_return", "fwd_vol"):
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)
    return df.drop(columns=["_dr", "_px", "_cl"])


def _positive(s: pd.Series) -> pd.Series:
    """0 이하와 무한대를 결측으로. 가격·수량에 쓰는 공통 가드입니다."""
    return s.where((s > 0) & np.isfinite(s))


def cross_sectional_rank(
    df: pd.DataFrame, columns: list[str], *, date_col: str = "date"
) -> pd.DataFrame:
    """일자별 횡단면 순위 정규화 (0~1).

    왜 필요한가:
        원값을 그대로 쓰면 시장 전체가 급등락한 날의 특성값이 모형을 지배합니다.
        또 변동성·거래대금 같은 변수는 분포가 시간에 따라 크게 이동하므로
        (레짐 변화), 학습 구간의 스케일이 예측 구간에 맞지 않게 됩니다.
        횡단면 순위는 이 두 문제를 동시에 완화합니다.

    누수 주의:
        순위는 **같은 날짜 안에서만** 계산됩니다. 전체 기간에 대해 정규화하면
        미래 분포 정보가 새어 들어갑니다.
    """
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue
        out[col] = out.groupby(date_col, sort=False)[col].transform(
            lambda s: s.rank(pct=True)
        )
    return out


def feature_columns(
    market: str, *, include_aux: bool = True, include_flow: bool = True
) -> list[str]:
    """시장별 사용 특성 목록.

    한국은 수급, 미국은 내부자 특성이 붙습니다. 둘 다 **보조**이며 핵심 3축
    (모멘텀·유동성·변동성)이 메인입니다.
    """
    cols = list(CORE_FEATURES)
    if include_aux:
        cols += AUX_FEATURES
    if include_flow:
        if market.upper() == "KR":
            cols += FLOW_FEATURES
        elif market.upper() == "US":
            cols += INSIDER_FEATURES
    return cols
