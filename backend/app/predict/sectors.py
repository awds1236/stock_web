"""섹터·카테고리 단위 예측.

왜 섹터를 1급 대상으로 두는가 -- 근거:
    섹터·국가 단위의 아웃오브샘플 R² 는 **0.29~0.95%** 로 보고되며, 이는 개별
    종목 수준(월간 0.33~0.40%)과 견주어 **동등하거나 더 낫습니다**. 개별 종목의
    특이 노이즈가 집계 과정에서 상쇄되기 때문입니다.

    즉 "어떤 종목이 오를까"보다 "어떤 업종이 오를까"가 근거상 더 다룰 만한
    질문입니다.

반드시 함께 기억할 반론:
    불확실성을 제대로 반영하지 않으면 **횡단면 섹터 예측력의 증거는 거의 남지
    않는다**는 연구가 있습니다. 그래서 이 모듈의 산출물은 점 예측이 아니라
    **구간과 보정된 확률**이며, 예측 구간의 폭을 항상 함께 제시합니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def attach_sector(panel: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """종목-섹터 매핑을 패널에 결합.

    Args:
        mapping: columns = [ticker, sector] (+ 선택적으로 date -- 시점별 매핑)

    시점별 매핑 주의:
        섹터 분류는 시간에 따라 바뀝니다. 현재 시점의 분류를 과거 전체에
        적용하면 미래 정보를 쓰는 것이 됩니다(예: 지금 'AI' 로 분류된 기업을
        10년 전에도 AI 로 취급). `date` 컬럼이 있으면 시점별로 결합합니다.
    """
    if "date" in mapping.columns:
        out = panel.merge(mapping, on=["ticker", "date"], how="left")
    else:
        out = panel.merge(mapping[["ticker", "sector"]], on="ticker", how="left")
    return out


def aggregate_to_sector(
    panel: pd.DataFrame,
    *,
    weight: str = "equal",
    value_col: str = "market_cap",
    group_col: str = "sector",
) -> pd.DataFrame:
    """종목 패널을 섹터 × 일자 시계열로 집계.

    Args:
        weight: 'equal' 동일가중 | 'value' 시가총액 가중

    동일가중과 시총가중은 다른 질문에 답합니다. 동일가중은 '그 업종의 전형적인
    종목', 시총가중은 '그 업종에 자본을 배분했을 때'의 수익률입니다. 기본값을
    동일가중으로 둔 이유는 소수 대형주가 업종 시그널을 지배하는 것을 막기
    위해서입니다.
    """
    if group_col not in panel.columns:
        raise ValueError(f"{group_col} 컬럼이 없습니다. attach_sector 를 먼저 호출하십시오.")

    df = panel.copy()
    # 대분류(sector) 외에 세분류(industry)로도 같은 집계를 씁니다. 출력 컬럼명은
    # 'sector' 로 통일해 하위 함수(상대강도·breadth)가 그대로 동작하게 합니다.
    if group_col != "sector":
        df["sector"] = df[group_col]
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["sector"])
    if df.empty:
        return pd.DataFrame(columns=["date", "sector", "ret", "n_constituents"])

    g = df.groupby("ticker", sort=False, group_keys=False)
    df["_ret"] = g["close"].transform(lambda s: s.pct_change())

    if weight == "value" and value_col in df.columns:
        df["_w"] = df[value_col].fillna(0.0)
    else:
        df["_w"] = 1.0

    def _agg(group: pd.DataFrame) -> pd.Series:
        valid = group.dropna(subset=["_ret"])
        total_w = valid["_w"].sum()
        ret = (
            float((valid["_ret"] * valid["_w"]).sum() / total_w)
            if total_w > 0
            else np.nan
        )
        return pd.Series({"ret": ret, "n_constituents": int(len(valid))})

    out = (
        df.groupby(["date", "sector"], sort=True)[["_ret", "_w"]]
        .apply(lambda x: _agg(x.assign(_ret=x["_ret"], _w=x["_w"])))
        .reset_index()
    )
    return out


def sector_index(sector_returns: pd.DataFrame, base: float = 100.0) -> pd.DataFrame:
    """섹터 수익률을 지수 시계열로 변환 (차트·지표 계산용).

    지표 라이브러리(`app/indicators/price.py`)가 가격 시계열을 받으므로, 섹터에
    같은 지표를 적용하려면 지수 형태가 필요합니다. 종목과 섹터에 **동일한 지표
    코드**를 쓰는 것이 목적입니다 -- 지표를 두 벌 유지하면 한쪽에만 수정이
    반영되는 사태가 옵니다.
    """
    df = sector_returns.copy().sort_values(["sector", "date"])
    df["close"] = df.groupby("sector", sort=False)["ret"].transform(
        lambda s: base * (1 + s.fillna(0.0)).cumprod()
    )
    # 지표 함수들이 기대하는 스키마에 맞춥니다 (섹터는 OHLC 구분이 없으므로 동일값)
    df["ticker"] = df["sector"]
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    return df[["date", "ticker", "sector", "open", "high", "low", "close", "ret",
               "n_constituents"]]


def sector_breadth(panel: pd.DataFrame, *, group_col: str = "sector") -> pd.DataFrame:
    """섹터별 상승 종목 비율 (breadth).

    해석:
        섹터 수익률이 양(+)인데 breadth 가 낮으면, 소수 종목이 그 업종을 끌어
        올렸다는 뜻입니다. 업종 전반의 강세와 몇 종목의 강세는 다른 사건이며,
        섹터 시그널을 종목으로 옮길 때 이 구분이 중요합니다.
    """
    if group_col not in panel.columns:
        raise ValueError(f"{group_col} 컬럼이 없습니다.")
    df = panel.copy()
    if group_col != "sector":
        df["sector"] = df[group_col]
    df["date"] = pd.to_datetime(df["date"])
    df["_ret"] = df.groupby("ticker", sort=False, group_keys=False)["close"].transform(
        lambda s: s.pct_change()
    )
    out = (
        df.dropna(subset=["_ret", "sector"])
        .groupby(["date", "sector"], sort=True)["_ret"]
        .agg(advancing=lambda s: float((s > 0).mean()), n="size")
        .reset_index()
    )
    return out


def relative_strength(
    sector_returns: pd.DataFrame, window: int = 60
) -> pd.DataFrame:
    """시장 대비 섹터 상대강도 (누적 초과수익).

    섹터 로테이션 분석의 기본 지표입니다. 절대 수익률로 섹터를 고르면 시장이
    전체적으로 오른 구간에서 모든 섹터가 '좋아 보이게' 됩니다.
    """
    df = sector_returns.copy().sort_values(["sector", "date"])
    market = df.groupby("date", sort=True)["ret"].transform("mean")
    df["excess"] = df["ret"] - market
    df["rs_" + str(window)] = df.groupby("sector", sort=False)["excess"].transform(
        lambda s: s.rolling(window, min_periods=window // 2).sum()
    )
    return df
