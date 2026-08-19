"""보드별 유니버스와 한국 당일 시세.

고정하려는 사실은 셋입니다.

1. **보드가 저장된다.** KRX 응답은 어떤 종목이 유가증권이고 어떤 종목이
   코스닥인지 알려주는데, 예전에는 저장 직전에 'KR' 로 덮여 사라졌습니다.
   그 정보가 없으면 (a) 보드별 상한을 걸 수 없고 (b) 현재가 심볼의 접미사를
   고를 수 없습니다.

2. **상한은 보드별이다.** KOSPI 200 / KOSDAQ 50. 하나의 상한으로 시총 줄을
   세우면 코스닥이 사실상 사라집니다.

3. **상한이 줄면 저장소도 줄어든다.** 수집은 '이미 저장된 종목 목록'을
   따르므로, 상한만 바꾸면 이미 쌓인 DB 는 예전 크기를 영원히 유지합니다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.ingest import pipeline
from app.store import Store


def _panel(day: date, tickers: list[tuple[str, str, float]]) -> pd.DataFrame:
    """(코드, 보드, 시총) 목록으로 하루치 패널을 만듭니다."""
    return pd.DataFrame(
        {
            "date": [day] * len(tickers),
            "ticker": [t for t, _, _ in tickers],
            "name": [f"종목{t}" for t, _, _ in tickers],
            "board": [b for _, b, _ in tickers],
            "open": [1000.0] * len(tickers),
            "high": [1000.0] * len(tickers),
            "low": [1000.0] * len(tickers),
            "close": [1000.0] * len(tickers),
            "volume": [1.0] * len(tickers),
            "value": [c for _, _, c in tickers],
            "market_cap": [c for _, _, c in tickers],
            "shares": [1.0] * len(tickers),
        }
    )


def _mixed(n_kospi: int, n_kosdaq: int, day: date) -> pd.DataFrame:
    """코스닥이 코스피보다 **작은** 시총을 갖도록 만듭니다.

    이렇게 해야 '하나의 풀로 자르면 코스닥이 사라진다'는 성질이 드러납니다.
    """
    rows: list[tuple[str, str, float]] = []
    for i in range(n_kospi):
        rows.append((f"1{i:05d}", "KOSPI", 1e12 - i))
    for i in range(n_kosdaq):
        rows.append((f"2{i:05d}", "KOSDAQ", 1e9 - i))
    return _panel(day, rows)


class TestBoardIsStored:
    def test_krx_provider_keeps_board_separate_from_market(self):
        """MKT_NM 을 market 으로 받으면 저장 시 'KR' 로 덮여 사라집니다."""
        from app.providers.krx_openapi import _PRICE_FIELD_MAP

        assert _PRICE_FIELD_MAP["MKT_NM"] == "board"
        assert "market" not in _PRICE_FIELD_MAP.values()

    def test_store_roundtrips_board(self, tmp_path):
        store = Store(path=tmp_path / "b.duckdb")
        store.upsert_prices("KR", _mixed(2, 2, date(2024, 1, 2)))
        uni = store.universe("KR")
        boards = dict(zip(uni["ticker"], uni["board"], strict=True))
        assert boards["100000"] == "KOSPI"
        assert boards["200000"] == "KOSDAQ"

    def test_board_survives_rows_that_predate_the_column(self, tmp_path):
        """보드 없이 저장된 과거 행이 섞여 있어도 유니버스는 보드를 압니다.

        집계가 any_value 면 NULL 을 뽑을 수 있습니다 -- 그러면 마이그레이션
        직후의 DB 에서 모든 종목의 보드가 비어 보입니다.
        """
        store = Store(path=tmp_path / "b.duckdb")
        old = _mixed(1, 0, date(2024, 1, 2)).drop(columns=["board"])
        store.upsert_prices("KR", old)
        store.upsert_prices("KR", _mixed(1, 0, date(2024, 1, 3)))
        assert store.universe("KR")["board"].iloc[0] == "KOSPI"


class TestBoardLimits:
    def test_defaults_are_200_and_50(self):
        assert pipeline.KR_KOSPI_LIMIT == 200
        assert pipeline.KR_KOSDAQ_LIMIT == 50

    def test_each_board_is_capped_on_its_own(self):
        panel = _mixed(300, 300, date(2024, 1, 2))
        by_board = pipeline.kr_top_by_board(panel)
        assert len(by_board["KOSPI"]) == 200
        assert len(by_board["KOSDAQ"]) == 50

    def test_kosdaq_survives_being_smaller_than_kospi(self):
        """단일 상한이면 코스닥이 통째로 잘려나갑니다 -- 그것이 바꾼 이유입니다."""
        panel = _mixed(300, 300, date(2024, 1, 2))
        single_pool, _ = pipeline.limit_to_top_market_cap(panel, 250)
        assert (single_pool["board"] == "KOSDAQ").sum() == 0

        keep, _ = pipeline.select_kr_universe(panel)
        kept = panel[panel["ticker"].isin(keep)]
        assert kept[kept["board"] == "KOSDAQ"]["ticker"].nunique() == 50
        assert kept[kept["board"] == "KOSPI"]["ticker"].nunique() == 200

    def test_ranking_is_by_market_cap_within_a_board(self):
        panel = _panel(
            date(2024, 1, 2),
            [("100000", "KOSPI", 1.0), ("100001", "KOSPI", 9.0)],
        )
        assert pipeline.kr_top_by_board(panel, kospi_limit=1)["KOSPI"] == ["100001"]

    def test_falls_back_to_one_pool_when_board_is_unknown(self):
        """보드를 모른다고 유니버스를 비우면 화면 전체가 빕니다."""
        panel = _mixed(5, 5, date(2024, 1, 2)).drop(columns=["board"])
        keep, _ = pipeline.select_kr_universe(panel, kospi_limit=3, kosdaq_limit=2)
        assert len(keep) == 5


@pytest.fixture
def no_classification(monkeypatch):
    monkeypatch.setattr(
        pipeline, "fetch_kr_classification",
        lambda *a, **k: pd.DataFrame(columns=["ticker", "industry"]),
    )


class TestUniverseShrinkMigration:
    """이미 300종목이 쌓인 DB 를 200/50 으로 옮기는 경로.

    실제 배포의 CI 캐시가 정확히 이 상태입니다. 이 경로가 없으면 새 상한은
    **새로 만든 DB 에만** 적용되고, 배포된 사이트는 영원히 예전 목록입니다.
    """

    def _run(self, store, panel, monkeypatch, **kwargs):
        class Fake:
            def fetch_daily_prices(self, trade_date: date) -> pd.DataFrame:
                out = panel.copy()
                out["date"] = trade_date
                return out

        monkeypatch.setattr(
            "app.providers.krx_openapi.KrxOpenApiPriceProvider", lambda *a, **k: Fake()
        )
        return pipeline.ingest_kr_prices(days=6, store=store, **kwargs)

    def test_shrinking_the_limit_prunes_the_stored_universe(
        self, tmp_path, monkeypatch, no_classification
    ):
        store = Store(path=tmp_path / "m.duckdb")
        panel = _mixed(10, 10, date.today() - timedelta(days=1))

        self._run(store, panel, monkeypatch, kospi_limit=10, kosdaq_limit=10)
        assert store.universe("KR")["ticker"].nunique() == 20

        res = self._run(store, panel, monkeypatch, kospi_limit=2, kosdaq_limit=1)
        uni = store.universe("KR")
        assert uni["ticker"].nunique() == 3
        assert (uni["board"] == "KOSPI").sum() == 2
        assert (uni["board"] == "KOSDAQ").sum() == 1
        assert any("제거" in w for w in res.warnings), "무엇이 지워졌는지 알려야 합니다"

    def test_a_board_that_answered_nothing_is_not_wiped_out(
        self, tmp_path, monkeypatch, no_classification
    ):
        """한쪽 엔드포인트만 실패한 날에 그 보드를 통째로 지우면 안 됩니다.

        KRX 는 보드별로 다른 엔드포인트입니다. 응답이 없었을 뿐인데 새
        유니버스에서 빠졌다고 삭제하면, 되돌리려면 그 히스토리를 처음부터
        다시 받아야 합니다.
        """
        store = Store(path=tmp_path / "p.duckdb")
        both = _mixed(5, 5, date.today() - timedelta(days=1))
        self._run(store, both, monkeypatch, kospi_limit=10, kosdaq_limit=10)
        assert store.universe("KR")["ticker"].nunique() == 10

        # 이번 배치에는 코스피만 들어왔고, 상한도 줄었습니다.
        kospi_only = both[both["board"] == "KOSPI"]
        self._run(store, kospi_only, monkeypatch, kospi_limit=2, kosdaq_limit=1)
        uni = store.universe("KR")
        assert (uni["board"] == "KOSPI").sum() == 2, "코스피는 상한대로 줄어야 합니다"
        assert (uni["board"] == "KOSDAQ").sum() == 5, "응답이 없던 보드는 보존"

    def test_universe_is_left_alone_when_it_already_fits(
        self, tmp_path, monkeypatch, no_classification
    ):
        """맞는 유니버스를 매번 다시 뽑으면 종목별 시계열에 구멍이 생깁니다."""
        store = Store(path=tmp_path / "m.duckdb")
        panel = _mixed(3, 2, date.today() - timedelta(days=1))
        self._run(store, panel, monkeypatch, kospi_limit=5, kosdaq_limit=5)
        first = set(store.universe("KR")["ticker"])

        # 순위가 뒤집힌 새 배치가 와도 유니버스는 그대로여야 합니다.
        flipped = panel.copy()
        flipped["market_cap"] = flipped["market_cap"].to_numpy()[::-1]
        res = self._run(store, flipped, monkeypatch, kospi_limit=5, kosdaq_limit=5)
        assert set(store.universe("KR")["ticker"]) == first
        assert not any("제거" in w for w in res.warnings)


class TestQuoteSymbols:
    """종목코드 -> 시세 소스 심볼.

    한국 종목코드('005930')는 그 자체로는 소스가 알아듣지 못합니다. 이 매핑이
    없던 것이 '당일 가격을 가져올 수 없다'의 직접적인 원인이었습니다.
    """

    def test_kospi_and_kosdaq_have_different_suffixes(self):
        from app.providers.quote import quote_symbols

        assert quote_symbols("KR", "005930", "KOSPI") == ["005930.KS"]
        assert quote_symbols("KR", "247540", "KOSDAQ") == ["247540.KQ"]

    def test_unknown_board_tries_both(self):
        from app.providers.quote import quote_symbols

        assert quote_symbols("KR", "005930") == ["005930.KS", "005930.KQ"]

    def test_us_ticker_is_used_as_is(self):
        from app.providers.quote import quote_symbols

        assert quote_symbols("US", "AAPL") == ["AAPL"]

    def test_board_matching_is_case_insensitive(self):
        from app.providers.quote import quote_symbols

        assert quote_symbols("KR", "005930", "kospi") == ["005930.KS"]


class TestQuoteRateLimit:
    """분당 1회. 무료 소스가 차단으로 응답하게 만드는 것은 대개 빈도입니다."""

    def test_second_call_within_a_minute_does_not_hit_the_source(self, monkeypatch):
        from app.providers import quote as q

        q.clear_quote_cache()
        calls: list[str] = []

        def fake(market, ticker, board):
            calls.append(ticker)
            return q.LiveQuote(1.0, 1.0, "KRW", "live", "지연 시세")

        monkeypatch.setattr(q, "_fetch_uncached", fake)
        q.fetch_quote("KR", "005930", "KOSPI")
        q.fetch_quote("KR", "005930", "KOSPI")
        assert calls == ["005930"], "60초 안의 두 번째 요청은 캐시여야 합니다"

    def test_cache_expires_so_the_price_still_moves(self, monkeypatch):
        from app.providers import quote as q

        q.clear_quote_cache()
        calls: list[str] = []
        monkeypatch.setattr(
            q, "_fetch_uncached",
            lambda market, ticker, board: (
                calls.append(ticker),
                q.LiveQuote(1.0, 1.0, "KRW", "live", ""),
            )[1],
        )
        now = [0.0]
        monkeypatch.setattr(q.time, "monotonic", lambda: now[0])
        q.fetch_quote("KR", "005930", "KOSPI")
        now[0] = 61.0
        q.fetch_quote("KR", "005930", "KOSPI")
        assert len(calls) == 2

    def test_different_tickers_are_cached_separately(self, monkeypatch):
        from app.providers import quote as q

        q.clear_quote_cache()
        calls: list[str] = []
        monkeypatch.setattr(
            q, "_fetch_uncached",
            lambda market, ticker, board: (
                calls.append(ticker),
                q.LiveQuote(1.0, 1.0, "KRW", "live", ""),
            )[1],
        )
        q.fetch_quote("KR", "005930", "KOSPI")
        q.fetch_quote("KR", "000660", "KOSPI")
        assert calls == ["005930", "000660"]
