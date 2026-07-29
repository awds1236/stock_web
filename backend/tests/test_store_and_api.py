"""저장소·분석 API 테스트.

핵심 검증:
  1. 재수집이 **멱등**한가 (중복 삽입 시 지표가 조용히 두 배가 되는 것을 방지)
  2. 예측 응답이 **품질 없이는 존재할 수 없는가** (스키마 수준 강제)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel
from fastapi.testclient import TestClient

from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(path=tmp_path / "t.duckdb")


@pytest.fixture
def seeded(store):
    # 종목 수를 40으로 둡니다. 12종목 같은 작은 횡단면에서는 아웃오브샘플 R²
    # 추정치의 분산이 매우 커서, 신호가 없는 데이터에서도 R² 2~3% 가 우연히
    # 나옵니다(확인함: 12종목 +0.028 → 40종목 -0.002 → 120종목 -0.002).
    # 비현실적으로 작은 유니버스로 테스트하면 누수 탐지가 무의미해집니다.
    panel = make_panel(n_days=1200, n_tickers=40, seed=5)
    sectors = ["Tech", "Fin", "Health"]
    panel["sector"] = panel["ticker"].map(
        {t: sectors[i % 3] for i, t in enumerate(sorted(panel["ticker"].unique()))}
    )
    panel["name"] = panel["ticker"] + " Inc"
    store.upsert_prices("US", panel)
    return store


@pytest.fixture
def client(seeded, monkeypatch):
    import app.store as store_mod

    monkeypatch.setattr(store_mod, "_store", seeded)
    from app.main import app

    return TestClient(app)


# ── 저장소 ────────────────────────────────────────────────────────────────
class TestStore:
    def test_upsert_is_idempotent(self, store):
        """같은 데이터를 두 번 넣어도 행 수가 늘면 안 됩니다.

        수집 잡은 실패·재시도가 잦습니다. 중복이 쌓이면 거래량과 수급이 두 배로
        집계되어 지표가 조용히 틀어집니다 -- 예외도 안 나므로 발견이 어렵습니다.
        """
        panel = make_panel(n_days=50, n_tickers=3, seed=1)
        first = store.upsert_prices("US", panel)
        store.upsert_prices("US", panel)
        assert len(store.prices("US")) == first

    def test_upsert_overwrites_with_latest_values(self, store):
        panel = make_panel(n_days=10, n_tickers=2, seed=2)
        store.upsert_prices("US", panel)
        revised = panel.copy()
        revised["close"] = 12345.0
        store.upsert_prices("US", revised)
        assert (store.prices("US")["close"] == 12345.0).all()

    def test_duplicate_keys_within_one_batch_are_collapsed(self, store):
        """한 배치 안의 중복도 걸러야 합니다."""
        panel = make_panel(n_days=5, n_tickers=2, seed=3)
        doubled = pd.concat([panel, panel], ignore_index=True)
        rows = store.upsert_prices("US", doubled)
        assert rows == len(panel)

    def test_markets_are_isolated(self, store):
        store.upsert_prices("US", make_panel(n_days=5, n_tickers=2, seed=4))
        store.upsert_prices("KR", make_panel(n_days=5, n_tickers=3, seed=5))
        assert store.prices("US")["ticker"].nunique() == 2
        assert store.prices("KR")["ticker"].nunique() == 3

    def test_universe_reports_coverage(self, seeded):
        u = seeded.universe("US")
        assert len(u) == 40
        assert (u["n_days"] > 0).all()
        assert u["sector"].notna().all()

    def test_empty_frame_is_a_noop(self, store):
        assert store.upsert_prices("US", pd.DataFrame()) == 0

    def test_flows_roundtrip(self, store):
        flows = pd.DataFrame(
            {
                "ticker": ["A", "A"],
                "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
                "investor_type": ["연기금", "외국인"],
                "buy_value": [100.0, 200.0],
                "sell_value": [50.0, 80.0],
                "net_value": [50.0, 120.0],
            }
        )
        store.upsert_flows("KR", flows)
        got = store.flows("KR", investor_type="연기금")
        assert len(got) == 1 and got["net_value"].iloc[0] == 50.0


# ── API ──────────────────────────────────────────────────────────────────
class TestAnalysisApi:
    def test_coverage_separates_missing_data_from_missing_key(self, client):
        """'데이터 없음'과 '인증키 없음'은 다른 문제이고 다른 조치가 필요합니다."""
        body = client.get("/api/coverage").json()
        us = next(c for c in body if c["market"] == "US")
        kr = next(c for c in body if c["market"] == "KR")
        assert us["ready"] is True and us["needs_credential"] is None
        assert kr["ready"] is False and kr["needs_credential"] == "KRX_AUTH_KEY"

    def test_universe_lists_tickers(self, client):
        body = client.get("/api/universe/US").json()
        assert len(body) == 40
        assert all(x["sector"] for x in body)

    def test_stock_detail_includes_caveats_with_every_indicator(self, client):
        """해석에는 반드시 한계가 따라붙어야 합니다.

        한계 없는 지표 해석은 매매 신호처럼 읽힙니다.
        """
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        body = client.get(f"/api/stocks/US/{ticker}").json()
        assert body["prices"] and body["interpretation"]
        for item in body["interpretation"]:
            assert item["caveat"], f"{item['indicator']} 에 한계 설명이 없습니다"

    def test_stock_detail_404_for_unknown_ticker(self, client):
        assert client.get("/api/stocks/US/NOPE").status_code == 404

    def test_unknown_market_is_404(self, client):
        assert client.get("/api/universe/JP").status_code == 404

    def test_forecast_always_carries_quality(self, client):
        """예측만 떼어 쓸 수 없어야 합니다 -- 이 앱의 핵심 계약입니다."""
        body = client.get("/api/forecast/US?target=direction&horizon_days=21").json()
        assert "quality" in body
        q = body["quality"]
        for field in ("brier", "reliability", "resolution", "skill_score", "ece"):
            assert field in q, f"{field} 가 품질 정보에 없습니다"
        assert q["literature_context"]

    def test_forecast_reports_useless_model_honestly(self, client):
        """랜덤워크 데이터에서 skill score 는 0 이하여야 합니다.

        여기서 양수가 나오면 파이프라인에 누수가 있다는 뜻입니다.
        """
        body = client.get("/api/forecast/US?target=direction").json()
        skill = body["quality"]["skill_score"]
        assert skill is not None
        assert skill < 0.05, f"신호 없는 데이터에서 skill={skill:.4f} -- 누수 의심"

    def test_forecast_return_target_reports_r2(self, client):
        body = client.get("/api/forecast/US?target=return&horizon_days=21").json()
        q = body["quality"]
        assert q["oos_r2"] is not None
        # 드리프트를 제거한 횡단면 지표가 함께 있어야 합니다
        assert q["oos_r2_cross"] is not None
        assert q["mean_daily_ic"] is not None
        # 랜덤워크에서 횡단면 R² 가 문헌 상단을 크게 넘으면 누수입니다
        assert q["leakage_warning"] is None
        # 기준선은 순위값 직접 비교가 아니라 모멘텀 단독 '모형'이어야 합니다.
        # (순위값 0~1 을 수익률 예측으로 쓰면 R² -3000% 같은 무의미한 수치가
        # 나옵니다 -- 실제 배포에서 확인된 버그의 회귀 테스트)
        assert q["baseline_label"] == "모멘텀 단독 모형"
        if q["baseline_r2"] is not None:
            assert q["baseline_r2"] > -1.0, "기준선 R² 가 비상식적입니다"

    def test_volatility_target_uses_persistence_not_return_yardstick(self, client):
        """변동성 예측의 판정 잣대는 수익률과 다릅니다.

        실제 배포에서 변동성 R² 78% 에 수익률 기준(0.4%) 누수 경고가 발동한
        버그의 회귀 테스트입니다. 변동성은 군집성 때문에 높은 R² 가 정상이며,
        기준선은 모멘텀이 아니라 지속성(현재 변동성 유지)이어야 합니다.
        """
        body = client.get("/api/forecast/US?target=volatility&horizon_days=21").json()
        q = body["quality"]
        # 수익률 잣대의 누수 경고가 변동성에 발동하면 안 됩니다
        assert q["leakage_warning"] is None
        assert q["baseline_label"] == "지속성 (현재 변동성 유지)"
        assert q["baseline_r2"] is not None
        # 변동성 문헌 안내가 수익률 안내와 달라야 합니다
        assert "군집" in q["literature_context"]

    def test_direction_target_reports_skill_not_r2(self, client):
        body = client.get("/api/forecast/US?target=direction&horizon_days=21").json()
        q = body["quality"]
        assert q["skill_score"] is not None
        assert q["oos_r2"] is None  # 방향의 판정 기준이 아님
        assert q["baseline_label"] is None  # skill score 가 이미 기저율 대비

    def test_forecast_on_market_without_data_is_409(self, client):
        """오류가 아니라 '데이터를 먼저 수집하라'는 안내여야 합니다."""
        resp = client.get("/api/forecast/KR")
        assert resp.status_code == 409
        assert "수집" in resp.json()["detail"]

    def test_sectors_endpoint(self, client):
        body = client.get("/api/sectors/US").json()
        assert {r["sector"] for r in body} == {"Tech", "Fin", "Health"}
        for r in body:
            assert r["n_constituents"] > 0
            assert r["breadth"] is None or 0 <= r["breadth"] <= 1

    def test_sectors_sorted_by_recent_return(self, client):
        body = client.get("/api/sectors/US").json()
        rets = [r["ret_20d"] for r in body if r["ret_20d"] is not None]
        assert rets == sorted(rets, reverse=True)

    def test_markets_endpoint_warns_against_cross_market_comparison(self, client):
        body = client.get("/api/markets").json()
        assert body["comparability_warning"]


def test_json_has_no_nan_values(client):
    """NaN 은 유효한 JSON 이 아닙니다. 프론트엔드가 파싱에 실패합니다."""
    for path in ("/api/coverage", "/api/sectors/US", "/api/forecast/US?target=return"):
        text = client.get(path).text
        assert "NaN" not in text and "Infinity" not in text, f"{path} 에 NaN/Infinity"


def test_store_reports_lock_conflict_actionably(tmp_path, monkeypatch):
    """DB 락은 불투명한 500 이 아니라 조치 방법이 담긴 오류여야 합니다.

    DuckDB 의 락은 **프로세스 간**에만 걸리므로 같은 프로세스에서 재현할 수
    없습니다. 그래서 연결 시점의 IOException 을 주입해 우리 쪽 처리를 검증합니다
    -- 검증 대상은 DuckDB 의 락 동작이 아니라 그것을 사용자에게 어떻게 전달하는가
    이기 때문입니다.
    """
    import duckdb

    from app.store import StoreLocked

    def boom(*_a, **_k):
        raise duckdb.IOException(
            'IO Error: Could not set lock on file "x": Conflicting lock is held'
        )

    monkeypatch.setattr(duckdb, "connect", boom)
    s = Store(path=tmp_path / "locked.duckdb")
    with pytest.raises(StoreLocked, match="다른 프로세스"):
        s.prices("US")


def test_non_lock_io_errors_are_not_swallowed(tmp_path, monkeypatch):
    """락이 아닌 IO 오류를 락으로 잘못 안내하면 엉뚱한 곳을 뒤지게 됩니다."""
    import duckdb

    from app.store import StoreLocked

    def boom(*_a, **_k):
        raise duckdb.IOException("IO Error: disk full")

    monkeypatch.setattr(duckdb, "connect", boom)
    s = Store(path=tmp_path / "x.duckdb")
    with pytest.raises(duckdb.IOException):
        s.prices("US")
    assert not isinstance(duckdb.IOException("x"), StoreLocked)


def test_numeric_helper_rejects_non_finite():
    from app.api.analysis import _f

    assert _f(np.nan) is None
    assert _f(np.inf) is None
    assert _f(1.5) == 1.5
    assert _f(None) is None
