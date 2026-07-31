"""종목 검색과 유니버스 상한 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel
from fastapi.testclient import TestClient

from app.ingest.pipeline import (
    DEFAULT_UNIVERSE_LIMIT,
    UNIVERSE_LIMIT_CAVEAT,
    limit_to_top_market_cap,
)
from app.store import Store


# ── 유니버스 상한 ────────────────────────────────────────────────────────
def panel_with_caps(n_tickers: int, n_days: int = 30, seed: int = 1) -> pd.DataFrame:
    """시가총액이 종목마다 다른 패널. 큰 순서대로 T000 이 가장 큽니다."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-01", periods=n_days)
    rows = []
    for i in range(n_tickers):
        cap = float((n_tickers - i) * 1e11)  # T000 이 최대
        rows.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "ticker": f"T{i:04d}",
                    "open": 1000 + rng.normal(0, 10, n_days),
                    "close": 1000 + rng.normal(0, 10, n_days),
                    "high": 1010.0,
                    "low": 990.0,
                    "volume": 1e5,
                    "value": cap / 1000,
                    "market_cap": cap,
                    "shares": 1e6,
                    "name": f"종목{i}",
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


class TestUniverseLimit:
    def test_keeps_only_top_n_by_market_cap(self):
        panel = panel_with_caps(500)
        out, kept = limit_to_top_market_cap(panel, limit=300)
        assert kept == 300
        assert out["ticker"].nunique() == 300
        # 가장 큰 종목은 남고, 가장 작은 종목은 빠져야 합니다
        assert "T0000" in set(out["ticker"])
        assert "T0499" not in set(out["ticker"])

    def test_keeps_full_history_of_selected_tickers(self):
        """선정된 종목은 **전 기간** 데이터가 남아야 합니다.

        최신 일자만 남기면 지표 계산이 불가능해집니다.
        """
        panel = panel_with_caps(50, n_days=30)
        out, _ = limit_to_top_market_cap(panel, limit=10)
        per_ticker = out.groupby("ticker").size()
        assert (per_ticker == 30).all()

    def test_no_op_when_universe_smaller_than_limit(self):
        panel = panel_with_caps(20)
        out, kept = limit_to_top_market_cap(panel, limit=300)
        assert kept == 20
        assert len(out) == len(panel)

    def test_falls_back_to_trading_value_without_market_cap(self):
        """시총이 없으면 거래대금으로 규모를 대신합니다."""
        panel = panel_with_caps(50)
        panel["market_cap"] = np.nan
        out, kept = limit_to_top_market_cap(panel, limit=10)
        assert kept == 10
        assert "T0000" in set(out["ticker"])  # 거래대금도 T0000 이 최대

    def test_empty_panel_is_safe(self):
        out, kept = limit_to_top_market_cap(pd.DataFrame(), limit=300)
        assert out.empty and kept == 0

    def test_caveat_mentions_survivorship(self):
        """상한 적용은 생존편향을 만듭니다 -- 문구가 그것을 말해야 합니다."""
        assert "생존편향" in UNIVERSE_LIMIT_CAVEAT
        assert DEFAULT_UNIVERSE_LIMIT == 300


# ── 검색 ─────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(path=tmp_path / "s.duckdb")
    panel = make_panel(n_days=40, n_tickers=6, seed=3)
    tickers = sorted(panel["ticker"].unique())
    names = {
        tickers[0]: "삼성전자",
        tickers[1]: "삼성물산",
        tickers[2]: "LG전자",
        tickers[3]: "SK하이닉스",
        tickers[4]: "카카오",
        tickers[5]: "네이버",
    }
    codes = {
        tickers[0]: "005930",
        tickers[1]: "028260",
        tickers[2]: "066570",
        tickers[3]: "000660",
        tickers[4]: "035720",
        tickers[5]: "035420",
    }
    inds = {
        tickers[0]: "반도체",
        tickers[1]: "건설",
        tickers[2]: "가전",
        tickers[3]: "반도체",
        tickers[4]: "인터넷",
        tickers[5]: "인터넷",
    }
    caps = {t: float((6 - i) * 1e12) for i, t in enumerate(tickers)}

    panel["name"] = panel["ticker"].map(names)
    panel["industry"] = panel["ticker"].map(inds)
    panel["sector"] = panel["industry"]
    panel["market_cap"] = panel["ticker"].map(caps)
    panel["ticker"] = panel["ticker"].map(codes)
    store.upsert_prices("KR", panel)

    import app.store as store_mod

    monkeypatch.setattr(store_mod, "_store", store)
    from app.main import app

    return TestClient(app)


def tickers_of(resp) -> list[str]:
    return [h["ticker"] for h in resp.json()]


class TestSearch:
    def test_exact_ticker_ranks_first(self, client):
        hits = client.get("/api/search/KR?q=005930")
        assert tickers_of(hits)[0] == "005930"
        assert hits.json()[0]["match"] == "ticker"

    def test_partial_ticker_prefix(self, client):
        """전부 입력하지 않아도 찾아야 합니다."""
        assert "005930" in tickers_of(client.get("/api/search/KR?q=0059"))

    def test_name_prefix_returns_all_matching(self, client):
        """'삼성' 만 쳐도 삼성 계열이 모두 나와야 합니다."""
        got = tickers_of(client.get("/api/search/KR?q=삼성"))
        assert "005930" in got and "028260" in got

    def test_name_substring_works(self, client):
        """'전자' 로도 삼성전자·LG전자가 잡혀야 합니다 (부분 일치)."""
        got = tickers_of(client.get("/api/search/KR?q=전자"))
        assert "005930" in got and "066570" in got

    def test_industry_search(self, client):
        """업종명으로도 검색됩니다."""
        got = tickers_of(client.get("/api/search/KR?q=반도체"))
        assert set(got) >= {"005930", "000660"}

    def test_ties_are_ordered_by_market_cap(self, client):
        """동점이면 큰 종목이 먼저 -- 사용자가 찾는 건 대개 대형주입니다."""
        got = tickers_of(client.get("/api/search/KR?q=인터넷"))
        # 카카오(4번째, 시총 2e12) > 네이버(5번째, 1e12)
        assert got.index("035720") < got.index("035420")

    def test_empty_query_returns_largest(self, client):
        """검색창을 열자마자 빈 화면이면 불친절합니다."""
        got = tickers_of(client.get("/api/search/KR?q="))
        assert got and got[0] == "005930"  # 시총 최대

    def test_case_insensitive(self, client):
        assert tickers_of(client.get("/api/search/KR?q=lg")) == tickers_of(
            client.get("/api/search/KR?q=LG")
        )

    def test_no_match_returns_empty_not_error(self, client):
        resp = client.get("/api/search/KR?q=존재하지않는종목")
        assert resp.status_code == 200 and resp.json() == []

    def test_limit_is_respected(self, client):
        assert len(client.get("/api/search/KR?q=&limit=2").json()) == 2

    def test_unknown_market_is_404(self, client):
        assert client.get("/api/search/JP?q=x").status_code == 404

    def test_market_without_data_returns_empty(self, client):
        assert client.get("/api/search/US?q=A").json() == []

    def test_hits_carry_industry_for_display(self, client):
        hit = client.get("/api/search/KR?q=005930").json()[0]
        assert hit["industry"] == "반도체"
        assert hit["market_cap"] is not None
