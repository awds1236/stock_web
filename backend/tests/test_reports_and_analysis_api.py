"""대상 단위 분석(종목/섹터/시장) 리포트와 그 API.

이 테스트가 지키는 계약:
    * 리포트는 **근거를 숨기지 않습니다** -- 규칙에 걸렸다면 어떤 규칙인지,
      한계가 무엇인지가 응답에 함께 있어야 합니다.
    * 종목 화면과 관찰 목록이 **같은 규칙 판정**을 씁니다. 두 곳이 갈리면
      사용자는 어느 쪽이 맞는지 알 방법이 없습니다.
    * 예측은 **선택**입니다. 기본 분석이 워크포워드를 기다리면 "버튼 눌러
      이 종목만 빠르게"라는 목적이 사라집니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel
from fastapi.testclient import TestClient

from app import reports
from app.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    st = Store(path=tmp_path / "r.duckdb")
    panel = make_panel(n_days=400, n_tickers=9, seed=5)
    tick = sorted(panel["ticker"].unique())
    industries = ["Semiconductors", "Banks - Diversified", "Biotechnology"]
    sectors = ["Technology", "Financial Services", "Healthcare"]
    panel["industry"] = panel["ticker"].map({t: industries[i % 3] for i, t in enumerate(tick)})
    panel["sector"] = panel["ticker"].map({t: sectors[i % 3] for i, t in enumerate(tick)})
    panel["name"] = panel["ticker"] + " Inc"

    # 명백한 상승 추세 종목 하나. 규칙 탐지력의 대조군입니다 -- 아무것도
    # 걸리지 않는 데이터로만 테스트하면 규칙이 죽어 있어도 통과합니다.
    #
    # 지수 성장으로 만든 이유: 직선 상승은 **최근 구간의 수익률이 오히려
    # 낮아져**(분모가 커지므로) 일간 변동성 2%의 랜덤워크에 60일 순위에서
    # 밀립니다. 상대강도 테스트가 뜻대로 동작하려면 최근 구간에서도 확실히
    # 앞서야 합니다.
    dates = sorted(panel["date"].unique())
    ramp = np.exp(np.linspace(np.log(100), np.log(10_000), len(dates)))
    winner = pd.DataFrame(
        {
            "date": dates,
            "ticker": "WINNER",
            "open": ramp,
            "close": ramp,
            "high": ramp * 1.01,
            "low": ramp * 0.99,
            "volume": 1e6,
            "value": 1e9,
            "market_cap": 1e11,
            "shares": 1e6,
            "sector": "Technology",
            "industry": "Semiconductors",
            "name": "Winner Inc",
        }
    )
    st.upsert_prices("US", pd.concat([panel, winner], ignore_index=True))

    import app.store as store_mod

    monkeypatch.setattr(store_mod, "_store", st)
    return st


@pytest.fixture
def client(store):
    from app.main import app

    return TestClient(app)


class TestStockReport:
    def test_report_carries_numbers_and_limits(self, store):
        r = reports.stock_report("US", "WINNER", store=store)
        assert r["ticker"] == "WINNER"
        assert r["price"]["close"] == pytest.approx(10_000, rel=1e-3)
        assert r["returns"]["20d"] is not None
        assert r["caveats"], "한계 없이 숫자만 내보내면 안 됩니다"
        for card in r["interpretation"]:
            assert card["caveat"]

    def test_uptrend_matches_rules(self, store):
        r = reports.stock_report("US", "WINNER", store=store)
        assert "정배열 (종가>20일선>60일선)" in r["rules"]["matched"]
        assert r["rules"]["score"] == len(r["rules"]["matched"])
        assert r["trend"]["stack"].startswith("정배열")

    def test_relative_block_compares_to_universe(self, store):
        r = reports.stock_report("US", "WINNER", store=store)
        rel = r["relative"]
        assert rel["rank_60d"]["rank"] == 1, "가장 강한 종목이 1위가 아니면 순위가 틀렸습니다"
        assert rel["excess_vs_universe_20d"] > 0

    def test_unknown_ticker_raises_lookup(self, store):
        with pytest.raises(LookupError):
            reports.stock_report("US", "NOPE", store=store)


class TestRuleParity:
    def test_watchlist_and_stock_report_agree(self, client, store):
        """같은 종목에 대해 두 화면의 판정이 갈리면 안 됩니다."""
        body = client.get("/api/watchlist/US").json()
        winner = next(c for c in body["candidates"] if c["ticker"] == "WINNER")
        report = reports.stock_report("US", "WINNER", store=store)
        assert sorted(winner["reasons"]) == sorted(report["rules"]["matched"])


class TestAnalysisApi:
    def test_stock_analysis_skips_forecast_by_default(self, client):
        body = client.get("/api/analyze/stock/US/WINNER").json()
        assert body["report"]["ticker"] == "WINNER"
        assert body["forecast"] is None
        assert body["forecast_error"] is None

    def test_stock_analysis_404_for_unknown_ticker(self, client):
        assert client.get("/api/analyze/stock/US/NOPE").status_code == 404

    def test_forecast_failure_does_not_kill_the_report(self, client):
        """데이터가 짧아 예측이 불가능해도 지표 분석은 나와야 합니다."""
        body = client.get(
            "/api/analyze/stock/US/WINNER?include_forecast=true&horizon_days=120"
        ).json()
        assert body["report"]["ticker"] == "WINNER"
        if body["forecast"] is None:
            assert body["forecast_error"], "실패했으면 이유가 있어야 합니다"

    def test_sector_analysis(self, client):
        body = client.get(
            "/api/analyze/sector/US?sector=Semiconductors&level=industry"
        ).json()
        assert body["report"]["sector"] == "Semiconductors"
        assert body["report"]["n_constituents"] >= 1
        assert body["report"]["caveats"]

    def test_sector_analysis_404_for_unknown_sector(self, client):
        assert client.get("/api/analyze/sector/US?sector=Nope").status_code == 404

    def test_market_analysis_lists_attention_with_reasons(self, client):
        body = client.get("/api/analyze/market/US").json()["report"]
        assert body["universe"]["n_tickers"] == 10
        assert body["index_proxy"]["ret_20d"] is not None
        assert body["internals"]["above_sma60_pct"] is not None
        for item in body["attention"]:
            assert item["reasons"], "이유 없는 주목 종목은 근거 없는 추천입니다"
            assert item["score"] == len(item["reasons"])

    def test_market_analysis_404_without_data(self, client):
        assert client.get("/api/analyze/market/KR").status_code == 404


class TestQuote:
    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        """테스트가 외부 시세 소스를 부르지 않게 합니다.

        끄지 않으면 이 클래스의 모든 테스트가 네트워크에 의존하고, 차단된
        환경에서는 수십 초를 기다렸다 실패합니다. 개별 테스트가 다시
        monkeypatch 하면 그쪽이 이깁니다.
        """
        from app.providers.quote import LiveQuote, clear_quote_cache

        clear_quote_cache()
        monkeypatch.setattr(
            "app.providers.quote.fetch_quote",
            lambda market, ticker, board=None: LiveQuote(
                None, None, None, "unavailable", "테스트: 외부 호출 없음"
            ),
        )

    @pytest.fixture
    def kr(self, client, store):
        panel = make_panel(n_days=30, n_tickers=3, seed=1)
        panel["name"] = "테스트"
        panel["board"] = "KOSPI"
        store.upsert_prices("KR", panel)
        return sorted(panel["ticker"].unique())

    def test_kr_falls_back_to_stored_close_with_reason(self, client, kr, monkeypatch):
        """한국도 당일 시세를 시도합니다. 실패하면 **이유와 함께** 종가로."""
        from app.providers.quote import LiveQuote

        monkeypatch.setattr(
            "app.providers.quote.fetch_quote",
            lambda market, ticker, board=None: LiveQuote(
                None, None, None, "unavailable", "소스 차단됨"
            ),
        )
        body = client.get(f"/api/quote/KR/{kr[0]}").json()
        assert body["source"] == "stored"
        assert body["price"] is not None
        assert "소스 차단됨" in body["note"], "왜 실시간이 아닌지 설명해야 합니다"

    def test_kr_uses_live_price_when_the_source_answers(self, client, kr, monkeypatch):
        """예전에는 한국이 조회 자체를 하지 않았습니다 -- 그게 '당일 가격 없음'의 원인."""
        from app.providers.quote import LiveQuote

        monkeypatch.setattr(
            "app.providers.quote.fetch_quote",
            lambda market, ticker, board=None: LiveQuote(
                71_000.0, 70_000.0, "KRW", "live", "지연 시세"
            ),
        )
        body = client.get(f"/api/quote/KR/{kr[0]}").json()
        assert body["source"] == "live"
        assert body["price"] == 71_000.0
        assert body["currency"] == "KRW"

    def test_unknown_ticker_is_404(self, client):
        assert client.get("/api/quote/US/NOPE").status_code == 404

    def test_batch_returns_one_row_per_known_ticker(self, client, kr):
        body = client.get(f"/api/quotes/KR?tickers={','.join(kr)}").json()
        assert [r["ticker"] for r in body] == kr

    def test_batch_skips_unknown_instead_of_failing_all(self, client, kr):
        """목록에 모르는 종목 하나가 있다고 표 전체의 가격이 사라지면 안 됩니다."""
        resp = client.get(f"/api/quotes/KR?tickers=NOPE,{kr[0]}")
        assert resp.status_code == 200
        assert [r["ticker"] for r in resp.json()] == [kr[0]]

    def test_batch_is_capped(self, client, kr, monkeypatch):
        """상한이 없으면 한 번의 요청이 수백 번의 외부 호출이 됩니다."""
        from app.api import analysis as api

        monkeypatch.setattr(api, "MAX_BATCH_QUOTES", 2)
        body = client.get(f"/api/quotes/KR?tickers={','.join(kr)}").json()
        assert len(body) == 2

    def test_batch_of_nothing_is_empty_not_error(self, client):
        assert client.get("/api/quotes/KR?tickers=").json() == []

    def test_us_batch_falls_back_when_source_unreachable(self, client, monkeypatch):
        """시세 소스가 막혀도 가격 칸이 비면 안 됩니다 -- 출처만 바뀝니다."""
        from app.providers.quote import LiveQuote

        monkeypatch.setattr(
            "app.providers.quote.fetch_quote",
            lambda market, ticker, board=None: LiveQuote(
                None, None, None, "unavailable", "차단됨"
            ),
        )
        body = client.get("/api/quotes/US?tickers=WINNER").json()
        assert body[0]["source"] == "stored"
        assert body[0]["price"] is not None
        assert "차단됨" in body[0]["note"], "왜 저장값인지 밝혀야 합니다"

    def test_us_batch_uses_live_price_when_available(self, client, monkeypatch):
        from app.providers.quote import LiveQuote

        monkeypatch.setattr(
            "app.providers.quote.fetch_quote",
            lambda market, ticker, board=None: LiveQuote(
                123.0, 100.0, "USD", "live", "지연 시세"
            ),
        )
        body = client.get("/api/quotes/US?tickers=WINNER").json()
        assert body[0]["source"] == "live"
        assert body[0]["price"] == 123.0
        assert body[0]["change_pct"] == pytest.approx(0.23)


class TestCorsOrigins:
    """정적 배포에서 이 백엔드를 부르려면 그 출처가 허용되어야 합니다."""

    def test_local_dev_is_always_allowed(self):
        from app.config import Settings

        assert "http://localhost:3000" in Settings(cors_origins="").cors_origin_list()

    def test_extra_origins_are_parsed_and_normalized(self):
        from app.config import Settings

        got = Settings(
            cors_origins="https://a.github.io/ , https://b.dev"
        ).cors_origin_list()
        assert "https://a.github.io" in got
        assert "https://b.dev" in got


def _fake_forecast():
    from app.api.analysis import ForecastOut, ForecastQuality

    return ForecastOut(
        market="US",
        target="direction",
        horizon_days=21,
        latest=[{"date": "2024-01-01", "ticker": f"T{i}", "prediction": 0.5} for i in range(9)],
        quality=ForecastQuality(
            n_folds=1, n_predictions=10, oos_r2=None, oos_r2_cross=None,
            mean_daily_ic=None, brier=0.25, reliability=0.0, resolution=0.0,
            skill_score=0.0, ece=0.0, reliability_curve=[], baseline_label=None,
            baseline_r2=None, beats_baseline=None, leakage_warning=None,
            literature_context="테스트",
        ),
        caveat="테스트",
    )


class TestForecastCache:
    """워크포워드는 이 앱에서 가장 비싼 연산입니다. 재사용되지 않으면 종목별
    예측은 쓸 수 없는 기능이 되고, 반대로 데이터가 바뀌었는데 재사용되면
    화면의 시세와 예측이 다른 날짜를 가리킵니다."""

    @pytest.fixture
    def counted(self, monkeypatch, store):
        from app.api import analysis as api

        api.invalidate_forecast_cache()
        calls = {"n": 0}

        def counting(market, target, horizon):
            calls["n"] += 1
            return _fake_forecast()

        monkeypatch.setattr(api, "_compute_forecast", counting)
        return api, calls

    def test_same_data_computes_once(self, counted):
        api, calls = counted
        api.cached_forecast("US", "direction", 21)
        api.cached_forecast("US", "direction", 21)
        assert calls["n"] == 1

    def test_new_data_invalidates(self, counted, store):
        api, calls = counted
        api.cached_forecast("US", "direction", 21)

        fresh = make_panel(n_days=3, n_tickers=1, seed=77)
        fresh["ticker"] = "NEWCO"
        fresh["date"] = pd.to_datetime(fresh["date"]) + pd.Timedelta(days=5000)
        store.upsert_prices("US", fresh)

        api.cached_forecast("US", "direction", 21)
        assert calls["n"] == 2, "새 시세가 들어왔는데 옛 예측을 재사용하면 안 됩니다"

    def test_top_n_slices_the_cached_result(self, counted, client):
        api, calls = counted
        body = client.get("/api/forecast/US?top_n=3").json()
        assert len(body["latest"]) == 3
        client.get("/api/forecast/US?top_n=9")
        assert calls["n"] == 1, "top_n 이 달라도 다시 계산할 이유가 없습니다"
