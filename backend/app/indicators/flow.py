"""수급(투자자별 매매동향) 지표 -- 이 앱의 핵심.

각 함수의 docstring 은 '이 지표를 어떻게 해석하는가'와 '무엇을 근거로 그렇게
해석하는가'를 담습니다. 이 내용이 그대로 docs/methodology.md 와 앱의 설명 UI 로
흘러가므로, 근거 없는 해석을 여기 적으면 안 됩니다.

공통 전제 -- 데이터 지연:
    종목별 연기금 수급은 장 마감 후 확정치입니다(확정 18:00 이후). 따라서 T일
    수급으로 만든 시그널은 T+1 시가에야 실행할 수 있습니다. 여기 함수들은 T일까지의
    데이터로 T일 지표를 계산하며, 시점 이동(shift)은 백테스트 엔진이 담당합니다.
    이 분업을 어기면 look-ahead 누수가 생깁니다.

입력 형식 (별도 명시가 없으면):
    net: 종목 × 일자 순매수대금 (원). index=date, 오름차순 정렬 가정.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def consecutive_net_buy_days(net: pd.Series) -> pd.Series:
    """연속 순매수 일수.

    해석:
        값이 클수록 해당 투자자가 그 종목을 며칠째 꾸준히 담고 있다는 뜻입니다.
        순매도로 돌아서면 0으로 리셋됩니다. 음수는 반환하지 않으며, 순매도 연속
        일수가 필요하면 `-net` 을 넣으십시오.

    해석상의 주의:
        연속 매수는 '기관이 확신을 갖고 있다'는 신호로 흔히 소비되지만, 리밸런싱·
        지수편입 대응 같은 기계적 매매일 수도 있습니다. 연속일수 자체보다 규모
        (net_buy_ratio)와 결합해서 보아야 합니다. 이 지표 단독의 유효성은 백테스트
        가설 H1 에서 검증 대상입니다.
    """
    positive = (net > 0).astype(int)
    # 순매수가 끊긴 지점마다 그룹을 새로 시작해 누적합
    blocks = (positive == 0).cumsum()
    return positive.groupby(blocks).cumsum().astype(int)


def net_buy_ratio(net: pd.Series, trading_value: pd.Series) -> pd.Series:
    """순매수대금 / 해당 종목 거래대금.

    해석:
        그날 그 종목에서 일어난 거래 중 해당 투자자의 순매수가 차지한 비중입니다.
        0.05 면 거래대금의 5%가 그 주체의 순매수였다는 뜻입니다.

    왜 절대금액이 아니라 비율인가:
        연기금이 삼성전자를 100억 순매수한 것과 중형주를 100억 순매수한 것은 전혀
        다른 사건입니다. 절대금액으로 랭킹을 만들면 상위권이 대형주로만 채워지고
        시그널이 사실상 '시가총액 순위'가 되어버립니다. 이 편향을 제거하는 것이
        백테스트 가설 H2 의 검증 내용입니다.
    """
    denom = trading_value.replace(0, np.nan)
    return net / denom


def net_buy_to_mktcap(net: pd.Series, market_cap: pd.Series) -> pd.Series:
    """순매수대금 / 시가총액.

    해석:
        해당 투자자가 그 기업의 지분을 하루에 몇 % 사들였는지에 대한 근사치입니다.
        거래대금 대비 비율(net_buy_ratio)이 '오늘 거래에서의 존재감'이라면, 이 지표는
        '기업 규모 대비 매집 강도'입니다. 거래가 한산한 종목에서 두 지표가 크게
        갈리므로 함께 봅니다.
    """
    denom = market_cap.replace(0, np.nan)
    return net / denom


def flow_persistence(net: pd.Series, window: int = 20) -> pd.Series:
    """순매수 부호의 지속성 (rolling window 내 순매수일 비중, -1 ~ +1).

    해석:
        +1 에 가까우면 창 구간 내내 순매수, -1 이면 내내 순매도, 0 이면 방향이
        오락가락했다는 뜻입니다. 연속일수가 '최근 몇 일 연달아'만 보는 데 비해
        이 지표는 중간에 하루 끊긴 매집도 포착합니다.

    근거:
        한국 시장 연구에서 기관·외국인 매매 흐름의 강한 지속성(long memory)이
        보고되었고, 위기 국면에서 그 지속성이 강해지는 국면 전환이 관찰되었습니다.
        다만 '지속성이 있다'는 것과 '수익률을 예측한다'는 것은 별개이며, 후자의
        증거는 훨씬 약합니다.
    """
    sign = np.sign(net.fillna(0))
    return sign.rolling(window, min_periods=max(2, window // 2)).mean()


def amihud_illiquidity(
    returns: pd.Series, trading_value: pd.Series, window: int = 20
) -> pd.Series:
    """Amihud 비유동성 = mean(|수익률| / 거래대금).

    해석:
        값이 클수록 적은 거래대금으로도 가격이 크게 움직이는 종목, 즉 비유동적인
        종목입니다.

    왜 수급 분석에 이 지표가 필요한가:
        한국 시장 연구에서 외국인 순매수의 초과수익 크기가 **비유동성 구간을 따라
        단조 증가**하는 것이 확인되었습니다. 즉 수급 시그널의 유효성은 유동성에
        조건부입니다. 동시에 비유동 종목은 실제 체결이 어려워 백테스트 수익이
        과대계상되기 쉬운 곳이기도 합니다 -- 그래서 백테스트 엔진의 거래량 제약과
        반드시 함께 보아야 합니다. 이것이 가설 H4 의 분석 축입니다.
    """
    denom = trading_value.replace(0, np.nan)
    daily = returns.abs() / denom
    return daily.rolling(window, min_periods=max(2, window // 2)).mean()


def net_buy_streak_value(net: pd.Series) -> pd.Series:
    """현재 연속 순매수 구간 동안의 누적 순매수대금.

    해석:
        '며칠째'(연속일수)와 '얼마나'(누적금액)를 하나로 합친 값입니다. 3일간
        소액씩 산 것과 3일간 대규모로 산 것을 구분합니다.
    """
    positive = net > 0
    blocks = (~positive).cumsum()
    return net.where(positive, 0.0).groupby(blocks).cumsum()


def by_ticker(
    df: pd.DataFrame,
    func,
    *,
    value_cols: str | list[str],
    ticker_col: str = "ticker",
    date_col: str = "date",
    **kwargs,
) -> pd.Series:
    """종목별로 시계열 지표 함수를 적용하는 헬퍼.

    지표 함수들은 단일 종목의 시계열을 받는 순수함수입니다. 패널 데이터에
    적용하려면 종목별로 나눠야 하는데, 이때 **날짜 정렬을 빠뜨리면 rolling 계산이
    조용히 틀립니다**. 그 실수를 한 곳에서 막기 위한 헬퍼입니다.
    """
    cols = [value_cols] if isinstance(value_cols, str) else list(value_cols)
    ordered = df.sort_values([ticker_col, date_col])

    def _apply(group: pd.DataFrame) -> pd.Series:
        args = [group[c] for c in cols]
        return func(*args, **kwargs)

    result = ordered.groupby(ticker_col, group_keys=False, sort=False).apply(_apply)
    # 원본 인덱스 순서로 복원
    if isinstance(result, pd.DataFrame):
        result = result.stack(future_stack=True)
    return result.reindex(df.index)
