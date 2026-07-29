"""미국 주식 시세 provider (yfinance).

*** 반드시 읽어야 할 한계 -- 백테스트 결과를 무효화할 수 있습니다 ***

yfinance 는 **상장폐지된 종목의 데이터를 제공하지 않습니다.** 이것은 단순한
불편이 아니라 백테스트의 **생존편향(survivorship bias)** 을 직접 만들어냅니다.
백테스트 엔진은 상폐를 -100%로 인식하도록 만들어져 있지만, 애초에 상폐 종목이
데이터에 없으면 엔진이 인식할 대상 자체가 존재하지 않습니다.

    결과: 미국 유니버스로 돌린 백테스트 성과는 **체계적으로 과대평가**됩니다.
          망한 회사가 표본에서 통째로 빠지기 때문입니다.

완화 방법 (구현 순서대로 권장):
  1. 시점별(point-in-time) 지수 구성종목 목록을 별도로 확보해 유니버스를 고정
  2. 유료 데이터(예: 상폐 종목 포함 히스토리 제공 벤더)로 교체
  3. 최소한, 결과 리포트에 생존편향 경고를 항상 함께 출력  <- 현재 구현

또한 yfinance 는 비공식 스크래핑 라이브러리입니다. Yahoo 의 응답 규격 변경에
따라 예고 없이 깨질 수 있고, 상업적 이용 시 약관 검토가 필요합니다. 프로토타입
용도로만 사용하고, 검증 결과를 진지하게 쓸 단계가 되면 소스를 교체하십시오.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.providers.base import PriceProvider, ProviderError

SURVIVORSHIP_WARNING = (
    "yfinance 는 상장폐지 종목을 제공하지 않습니다. 이 데이터로 산출한 백테스트 "
    "성과에는 생존편향이 포함되어 있으며 실제보다 낙관적입니다."
)


class YFinancePriceProvider(PriceProvider):
    """일별 OHLCV. 배당·분할 조정가를 사용합니다.

    조정가를 쓰는 이유: 미조정 가격으로 수익률을 계산하면 분할일에 -50% 같은
    가짜 폭락이 생기고, 그것이 그대로 시그널과 성과에 반영됩니다.
    """

    def __init__(self, tickers: list[str] | None = None) -> None:
        self.tickers = tickers or []
        self._warned = False

    def _yf(self) -> Any:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderError(
                "yfinance 가 설치되지 않았습니다: uv pip install yfinance"
            ) from exc
        return yf

    def fetch_history(
        self,
        tickers: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """기간 일괄 조회. 일자별 호출보다 훨씬 효율적입니다.

        Returns:
            columns = [date, ticker, open, high, low, close, volume, value]
            `value` 는 거래대금 근사치(종가 × 거래량)입니다. 미국은 거래대금을
            직접 주지 않으므로 근사하며, 이 값이 거래량 제약의 기준이 됩니다.
        """
        if not tickers:
            return _empty_frame()

        yf = self._yf()
        if not self._warned:
            import warnings

            warnings.warn(SURVIVORSHIP_WARNING, stacklevel=2)
            self._warned = True

        raw = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,  # 분할·배당 조정
            progress=False,
            group_by="ticker",
            threads=True,
        )
        if raw is None or raw.empty:
            return _empty_frame()

        frames: list[pd.DataFrame] = []
        for ticker in tickers:
            try:
                sub = raw[ticker] if len(tickers) > 1 else raw
            except KeyError:
                continue
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            df = pd.DataFrame(
                {
                    "date": pd.to_datetime(sub.index).date,
                    "ticker": ticker,
                    "open": sub["Open"].to_numpy(),
                    "high": sub["High"].to_numpy(),
                    "low": sub["Low"].to_numpy(),
                    "close": sub["Close"].to_numpy(),
                    "volume": sub["Volume"].to_numpy(),
                }
            )
            frames.append(df)

        if not frames:
            return _empty_frame()

        out = pd.concat(frames, ignore_index=True)
        # 거래대금 근사. 미국은 원천 거래대금을 주지 않으므로 종가 × 거래량으로
        # 대신합니다. 장중 가격 변동을 무시하는 근사이지만 거래량 제약 용도로는
        # 충분하며, 과대추정보다는 보수적입니다.
        out["value"] = out["close"] * out["volume"]
        return out.dropna(subset=["open", "close"]).reset_index(drop=True)

    def fetch_daily_prices(self, trade_date: date) -> pd.DataFrame:
        """PriceProvider 프로토콜 준수용 단일 일자 조회.

        yfinance 는 기간 조회가 훨씬 효율적이므로 실제 수집에는
        `fetch_history` 를 쓰십시오.
        """
        from datetime import timedelta

        df = self.fetch_history(self.tickers, trade_date, trade_date + timedelta(days=1))
        return df[df["date"] == trade_date].reset_index(drop=True)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["date", "ticker", "open", "high", "low", "close", "volume", "value"]
    )
