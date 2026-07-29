"""가격 기반 지표 (추세·모멘텀·변동성·거래량).

리서치가 말하는 것을 먼저 적어둡니다 -- 이 파일의 지표들을 과신하지 않기 위해서입니다:

  * 한국 시장 연구에서 **기술적 지표는 경제 변수보다 주식프리미엄 예측력이 낮고**,
    경기국면을 구분하고 주성분을 결합했을 때에만 유의한 개선이 관찰되었습니다.
  * SHAP 기반 분석에서 즉각적 가격 변수가 예측력을 지배하고 **시차 기술적 지표의
    설명력은 제한적**이었습니다.
  * Quantopian 888개 전략 분석에서 인샘플 샤프지수의 아웃오브샘플 예측력은
    R² < 0.025 였습니다.

따라서 이 지표들은 **단독 매매 신호가 아니라 필터·국면 구분용**으로 씁니다.
RSI 30 이하라서 산다 같은 규칙은 이 앱에서 신호로 제공하지 않습니다.

모든 함수는 순수함수이며 단일 종목 시계열(날짜 오름차순)을 받습니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """단순이동평균."""
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    """지수이동평균."""
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD (line, signal, histogram).

    해석:
        히스토그램 부호 전환이 추세 전환의 후행 확인 신호로 쓰입니다. '후행'이
        핵심입니다 -- MACD 는 이동평균의 차이이므로 구조적으로 가격을 뒤따릅니다.
        추세 판별용 필터로는 쓸모가 있지만 진입 타이밍 신호로는 근거가 약합니다.
    """
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI (Wilder 평활).

    해석과 그 한계:
        통념은 70 이상 과매수 / 30 이하 과매도이지만, **추세장에서는 RSI 가 과매수
        구간에 머무른 채 계속 오릅니다**. 역추세 매매 규칙으로 단독 사용하면
        상승장에서 조기 매도, 하락장에서 조기 매수를 반복합니다. 20년 이상
        백테스트를 수행한 실무 보고에서도 RSI 는 '추가 조건·필터와 결합했을 때에만'
        평균회귀 전략에 쓸 수 있다고 결론냅니다.

        이 앱에서는 진입 신호가 아니라 국면 서술 변수로 사용합니다.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder 평활 = alpha 1/window 의 EWM
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # 손실이 전혀 없는 구간은 RSI 100 으로 정의
    return out.where(avg_loss != 0, 100.0).where(avg_gain.notna())


def momentum_12_1(close: pd.Series, lookback: int = 252, skip: int = 21) -> pd.Series:
    """12-1 모멘텀: 최근 1개월을 제외한 12개월 수익률.

    근거:
        횡단면 모멘텀은 기술적 지표 중 학술적으로 가장 광범위하게 재현된 것에
        속합니다. 최근 1개월을 제외하는 이유는 단기 반전(short-term reversal)
        효과가 모멘텀 신호를 오염시키기 때문입니다.

    한국 시장 주의:
        국내 주식시장에서 모멘텀 효과는 미국 대비 약하거나 시장 유동성 국면에
        의존적이라는 연구가 있습니다. 무비판적으로 이식하면 안 됩니다.
    """
    return close.shift(skip) / close.shift(lookback) - 1


def realized_volatility(close: pd.Series, window: int = 20, periods: int = 252) -> pd.Series:
    """연율화 실현변동성."""
    return close.pct_change().rolling(window, min_periods=window).std() * np.sqrt(periods)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range.

    해석:
        방향이 아니라 변동폭을 재는 지표입니다. 손절폭·포지션 사이징의 기준으로
        쓰는 것이 본래 용도이며, 매매 방향 신호로는 쓰지 않습니다.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """볼린저 밴드 (mid, upper, lower, %b)."""
    mid = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std()
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    width = (upper - lower).replace(0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "pct_b": (close - lower) / width}
    )


def pct_from_52w_high(close: pd.Series, window: int = 252) -> pd.Series:
    """52주 신고가 대비 위치 (0 = 신고가, 음수 = 고점 대비 하락률).

    근거:
        52주 신고가 근접도는 모멘텀의 대용치로 문헌에서 널리 쓰이며, 수익률
        예측력이 보고된 바 있습니다.
    """
    rolling_high = close.rolling(window, min_periods=window // 2).max()
    return close / rolling_high - 1


def turnover(volume: pd.Series, shares: pd.Series) -> pd.Series:
    """거래회전율 = 거래량 / 상장주식수."""
    return volume / shares.replace(0, np.nan)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()
