"""내부자 매매 지표 (미국, SEC Form 4).

한국의 수급 지표(`flow.py`)와 **같은 것을 재지 않습니다.** 저쪽은 기관의 순매수,
이쪽은 내부자 개인의 거래입니다. 두 시장의 시그널을 나란히 놓고 우열을 비교하지
마십시오.

시점 규칙 -- 여기가 look-ahead 누수가 가장 나기 쉬운 곳입니다:
    Form 4는 거래 후 2영업일 내 제출됩니다. 따라서 거래일(transaction_date)에는
    아직 아무도 그 정보를 모릅니다. **시그널은 반드시 접수일(filed_date) 기준**으로
    집계해야 하며, 이 모듈의 모든 함수는 filed_date 를 시점 축으로 씁니다.
    거래일로 집계하면 공시 전 정보를 쓴 것이 되어 백테스트가 조용히 부풀려집니다.

근거와 그 한계:
    * 내부자 **매수(코드 P)** 는 6~12개월 초과수익 4~8% 가 일관되게 보고됨
    * **매도는 예측력이 약하거나 없음** -- 분산투자·세금·유동성 등 정보와 무관한
      동기가 많기 때문
    * 신호당 거래금액을 현실적 규모로 제한하면 초과수익이 사라지거나 음수로
      뒤집힌다는 연구가 있음 -> 가설 H7 에서 거래량 제약을 켜고 직접 검증
"""

from __future__ import annotations

import pandas as pd

BUY = "P"
SELL = "S"


def daily_insider_aggregate(txns: pd.DataFrame) -> pd.DataFrame:
    """거래 단위 데이터를 종목 × 접수일 단위로 집계.

    Args:
        txns: `sec_edgar.transactions_to_frame` 출력.

    Returns:
        columns = [date, ticker, buy_value, sell_value, net_value,
                   n_buyers, n_officer_buyers, n_director_buyers]
        `date` 는 **접수일**입니다 (거래일이 아님).
    """
    cols = [
        "date",
        "ticker",
        "buy_value",
        "sell_value",
        "net_value",
        "n_buyers",
        "n_officer_buyers",
        "n_director_buyers",
    ]
    if txns.empty:
        return pd.DataFrame(columns=cols)

    df = txns.copy()
    df["date"] = pd.to_datetime(df["filed_date"])
    buys = df[df["code"] == BUY]
    sells = df[df["code"] == SELL]

    grouped = df.groupby(["date", "ticker"], sort=False)
    out = pd.DataFrame(index=grouped.size().index)
    out["buy_value"] = buys.groupby(["date", "ticker"], sort=False)["value"].sum()
    out["sell_value"] = sells.groupby(["date", "ticker"], sort=False)["value"].sum()
    out = out.fillna(0.0)
    out["net_value"] = out["buy_value"] - out["sell_value"]

    # 매수자 수는 고유 인원 기준. 한 사람이 여러 건으로 쪼개 신고해도 1명입니다.
    out["n_buyers"] = buys.groupby(["date", "ticker"], sort=False)["insider_name"].nunique()
    out["n_officer_buyers"] = (
        buys[buys["is_officer"]]
        .groupby(["date", "ticker"], sort=False)["insider_name"]
        .nunique()
    )
    out["n_director_buyers"] = (
        buys[buys["is_director"]]
        .groupby(["date", "ticker"], sort=False)["insider_name"]
        .nunique()
    )
    out[["n_buyers", "n_officer_buyers", "n_director_buyers"]] = (
        out[["n_buyers", "n_officer_buyers", "n_director_buyers"]].fillna(0).astype(int)
    )

    return out.reset_index()[cols]


def cluster_buy_score(
    agg: pd.DataFrame, window: int = 90, min_buyers: int = 2
) -> pd.Series:
    """클러스터 매수: 최근 window 일 내 서로 다른 내부자 몇 명이 샀는가.

    해석:
        한 명의 매수보다 **여러 내부자가 독립적으로 사는 것**이 강한 신호로
        해석됩니다. 한 사람의 거래는 개인 사정(주택 구입 자금, 세금)일 수 있지만
        여러 명이 동시에 사는 것은 그 설명이 어렵기 때문입니다.

    Args:
        agg: `daily_insider_aggregate` 출력 (종목별로 날짜 오름차순 정렬 가정).
        min_buyers: 이 인원 미만이면 0 (클러스터 아님).
    """
    rolled = agg.groupby("ticker", sort=False)["n_buyers"].transform(
        lambda s: s.rolling(window, min_periods=1).sum()
    )
    return rolled.where(rolled >= min_buyers, 0).astype(float)


def buy_value_to_mktcap(agg_buy_value: pd.Series, market_cap: pd.Series) -> pd.Series:
    """내부자 매수금액 / 시가총액.

    왜 절대금액이 아닌가:
        대형주 CEO의 100만 달러 매수와 소형주 CEO의 100만 달러 매수는 다른
        사건입니다. 절대금액 랭킹은 대형주로만 채워집니다 -- 한국 수급 지표에서
        `net_buy_ratio` 를 쓰는 것과 같은 이유입니다.
    """
    return agg_buy_value / market_cap.replace(0, pd.NA)


def officer_weighted_buy(agg: pd.DataFrame, officer_weight: float = 2.0) -> pd.Series:
    """임원 매수에 가중치를 준 매수 강도.

    해석:
        임원(CEO/CFO 등)은 이사보다 회사 실적에 대한 정보 접근이 깊다고 보는 것이
        통상적 해석입니다. 다만 이 가중치는 **임의 파라미터**이며, 근거가 확립된
        값이 아닙니다. 그래서 기본값을 고정하지 않고 백테스트에서 탐색 대상으로
        두되, 탐색한 조합 수를 DSR 계산에 반드시 포함시켜야 합니다.
    """
    return (
        agg["n_officer_buyers"] * officer_weight + agg["n_director_buyers"]
    ).astype(float)


def days_since_last_buy(agg: pd.DataFrame) -> pd.Series:
    """마지막 내부자 매수 이후 경과 거래일 수.

    해석:
        값이 작을수록 최근에 매수가 있었다는 뜻입니다. 내부자 매수의 초과수익은
        6~12개월에 걸쳐 천천히 실현된다는 것이 문헌의 보고이므로, 이 값이 크다고
        곧바로 신호가 소멸했다고 보기는 어렵습니다.
    """

    # 종목 내 행 순번을 시간 축으로 씁니다(달력일이 아니라 관측일 기준).
    position = agg.groupby("ticker", sort=False).cumcount()
    last_buy = position.where(agg["buy_value"] > 0)
    last_buy = last_buy.groupby(agg["ticker"], sort=False).ffill()
    # 첫 매수 이전 구간은 NaN 입니다. 0으로 채우면 '방금 샀다'는 뜻이 되어
    # 매수가 없었던 종목이 최상위 신호로 둔갑합니다.
    return (position - last_buy).astype(float)
