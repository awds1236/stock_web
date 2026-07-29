"""데이터 수집 파이프라인.

시장별 가용성이 다릅니다 -- 이것이 사용자 경험을 크게 좌우하므로 코드에
명시합니다:

    미국: yfinance 만 있으면 되고 **인증키가 필요 없습니다.** 앱을 켜자마자
          바로 수집·분석이 가능합니다.
    한국: KRX 인증키가 있어야 합니다. 키가 없으면 수집 자체가 불가능하므로,
          UI 는 이 상태를 '오류'가 아니라 '설정 필요'로 안내해야 합니다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.providers.base import ProviderError
from app.store import Store, get_store

log = logging.getLogger(__name__)

# 기본 미국 유니버스. 인증키 없이 즉시 동작하는 경로를 만들기 위한 것이며,
# 실제 분석에서는 지수 구성종목으로 교체해야 합니다.
#
# 생존편향 경고: 이 목록은 '오늘 살아남은' 대형주입니다. 과거 시점에 존재했으나
# 상장폐지·피인수된 종목이 빠져 있으므로, 이 유니버스로 돌린 백테스트는
# 낙관적으로 편향됩니다. yfinance 가 상폐 종목을 제공하지 않는 문제와 겹칩니다.
DEFAULT_US_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "COST", "PEP", "KO", "ADBE", "CRM", "NFLX", "AMD",
    "INTC", "CSCO", "QCOM", "TXN", "AMAT", "MU", "LRCX", "KLAC",
    "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "SPGI",
    "LLY", "PFE", "TMO", "ABT", "DHR", "BMY", "AMGN", "GILD",
    "CAT", "DE", "BA", "HON", "GE", "LMT", "RTX", "UPS",
    "DIS", "CMCSA", "T", "VZ", "TMUS", "NKE", "SBUX", "MCD",
]


@dataclass
class IngestResult:
    market: str
    kind: str
    rows: int
    tickers: int
    start: date | None
    end: date | None
    warnings: list[str]


def ingest_us_prices(
    tickers: list[str] | None = None,
    *,
    years: int = 10,
    store: Store | None = None,
) -> IngestResult:
    """미국 시세 수집 (yfinance, 인증키 불필요).

    섹터 정보도 함께 채웁니다 -- 섹터 단위 예측이 이 앱의 주요 기능이므로
    시세만 있고 섹터가 비면 절반만 쓸 수 있게 됩니다.
    """
    from app.providers.yfinance_provider import (
        SURVIVORSHIP_WARNING,
        YFinancePriceProvider,
    )

    store = store or get_store()
    tickers = tickers or DEFAULT_US_UNIVERSE
    end = date.today()
    start = end - timedelta(days=int(365.25 * years))

    provider = YFinancePriceProvider()
    df = provider.fetch_history(tickers, start, end)
    warnings = [SURVIVORSHIP_WARNING]

    if df.empty:
        return IngestResult("US", "prices", 0, 0, None, None,
                            [*warnings, "수집된 데이터가 없습니다."])

    sectors = _fetch_us_sectors(tickers)
    df["sector"] = df["ticker"].map(sectors)
    missing_sector = int(df["sector"].isna().any())
    if missing_sector:
        warnings.append(
            "일부 종목의 섹터 정보를 가져오지 못했습니다. 해당 종목은 섹터 "
            "분석에서 제외됩니다."
        )

    # yfinance 는 시가총액 시계열을 주지 않습니다. 상장주식수를 알 수 없으므로
    # 시총은 비워둡니다 -- 종가로 임의 추정하면 규모 관련 특성이 조용히 틀립니다.
    rows = store.upsert_prices("US", df)
    store.log_ingest("US", "prices", end, rows)

    return IngestResult(
        market="US",
        kind="prices",
        rows=rows,
        tickers=int(df["ticker"].nunique()),
        start=df["date"].min(),
        end=df["date"].max(),
        warnings=warnings,
    )


def _fetch_us_sectors(tickers: list[str]) -> dict[str, str]:
    """yfinance 에서 종목별 섹터를 조회.

    시점별 매핑이 아니라 **현재 시점 분류**입니다. 과거 구간에 현재 분류를
    적용하면 엄밀히는 미래 정보가 섞이지만, 섹터 재분류는 드물고 무료로 얻을
    수 있는 대안이 없어 이 한계를 안고 씁니다. 문서와 API 응답에 명시합니다.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    out: dict[str, str] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            sector = info.get("sector")
            if sector:
                out[t] = str(sector)
        except Exception as exc:  # noqa: BLE001 -- 개별 종목 실패로 전체를 막지 않습니다
            log.debug("섹터 조회 실패 %s: %s", t, exc)
    return out


def ingest_kr_prices(
    *,
    days: int = 30,
    store: Store | None = None,
) -> IngestResult:
    """한국 시세 수집 (KRX Open API, **인증키 필요**)."""
    from app.providers.krx_openapi import KrxOpenApiPriceProvider

    store = store or get_store()
    provider = KrxOpenApiPriceProvider()

    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    end = date.today()
    for i in range(days):
        day = end - timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            df = provider.fetch_daily_prices(day)
        except ProviderError as exc:
            warnings.append(f"{day}: {exc}")
            continue
        if not df.empty:
            frames.append(df)

    if not frames:
        return IngestResult("KR", "prices", 0, 0, None, None,
                            warnings or ["수집된 데이터가 없습니다."])

    all_df = pd.concat(frames, ignore_index=True)
    rows = store.upsert_prices("KR", all_df)
    store.log_ingest("KR", "prices", end, rows)
    return IngestResult(
        market="KR",
        kind="prices",
        rows=rows,
        tickers=int(all_df["ticker"].nunique()),
        start=all_df["date"].min(),
        end=all_df["date"].max(),
        warnings=warnings,
    )
