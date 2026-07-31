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
    def test_kr_falls_back_to_stored_close_with_reason(self, client, store):
        panel = make_panel(n_days=30, n_tickers=2, seed=1)
        panel["name"] = "테스트"
        store.upsert_prices("KR", panel)
        ticker = sorted(panel["ticker"].unique())[0]

        body = client.get(f"/api/quote/KR/{ticker}").json()
        assert body["source"] == "stored"
        assert body["price"] is not None
        assert "장중" in body["note"], "왜 실시간이 아닌지 설명해야 합니다"

    def test_unknown_ticker_is_404(self, client):
        assert client.get("/api/quote/US/NOPE").status_code == 404


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
