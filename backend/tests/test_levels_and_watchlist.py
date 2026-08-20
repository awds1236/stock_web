"""지지/저항·이동평균 교차·관찰 목록 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import make_panel
from fastapi.testclient import TestClient

from app.indicators import levels as lv
from app.store import Store


# ── 이동평균 교차 ────────────────────────────────────────────────────────
class TestMaCross:
    def test_uptrend_after_downtrend_is_golden_with_recent_cross(self):
        """하락 후 강한 상승 전환 → 골든크로스가 감지되어야 합니다."""
        down = np.linspace(200, 100, 120)
        up = np.linspace(100, 260, 120)
        close = pd.Series(np.concatenate([down, up]))
        c = lv.ma_cross(close, fast=20, slow=60)
        assert c.state == "golden"
        assert c.last_cross == "golden"
        assert c.days_since_cross is not None and c.days_since_cross < 120

    def test_downtrend_after_uptrend_is_dead(self):
        up = np.linspace(100, 260, 120)
        down = np.linspace(260, 120, 120)
        close = pd.Series(np.concatenate([up, down]))
        c = lv.ma_cross(close, fast=20, slow=60)
        assert c.state == "dead"
        assert c.last_cross == "dead"

    def test_monotonic_series_has_state_but_no_cross(self):
        """줄곧 오르기만 한 종목은 정배열이지만 교차 이벤트는 없습니다."""
        close = pd.Series(np.linspace(100, 300, 300))
        c = lv.ma_cross(close, fast=20, slow=60)
        assert c.state == "golden"
        assert c.last_cross is None

    def test_short_history_is_insufficient_not_wrong(self):
        """데이터가 부족하면 틀린 답 대신 '판단 불가'를 반환해야 합니다."""
        close = pd.Series(np.linspace(100, 110, 30))
        assert lv.ma_cross(close, fast=20, slow=60).state == "insufficient"

    def test_recent_cross_tag_respects_window(self):
        down = np.linspace(200, 100, 120)
        up = np.linspace(100, 300, 200)  # 교차 후 오래 지남
        close = pd.Series(np.concatenate([down, up]))
        assert lv.recent_cross_tag(close, within_days=15) is None
        # 교차 직후 구간으로 자르면 감지되어야 합니다
        c = lv.ma_cross(close, fast=20, slow=60)
        cut = len(close) - c.days_since_cross + 5 if c.days_since_cross else len(close)
        assert lv.recent_cross_tag(close.iloc[:cut], within_days=15) == "golden"


# ── 지지/저항 ────────────────────────────────────────────────────────────
def make_ranging_series(levels=(100.0, 120.0), n_cycles=6, steps=15):
    """지정한 두 수준 사이를 왕복하는 합성 가격."""
    lo, hi = levels
    seg_up = np.linspace(lo, hi, steps)
    seg_dn = np.linspace(hi, lo, steps)
    closes = np.concatenate([np.concatenate([seg_up, seg_dn]) for _ in range(n_cycles)])
    close = pd.Series(closes)
    high = close * 1.002
    low = close * 0.998
    return high, low, close


class TestSwingLevels:
    def test_range_bound_series_finds_both_boundaries(self):
        """100~120 박스권이면 두 경계가 수준으로 잡혀야 합니다."""
        high, low, close = make_ranging_series()
        found = lv.swing_levels(high, low, close, pivot_window=3)
        assert found, "수준이 하나도 감지되지 않았습니다"
        prices = sorted(x.price for x in found[:2])
        assert prices[0] == pytest.approx(100, rel=0.03)
        assert prices[-1] == pytest.approx(120, rel=0.03)

    def test_kind_is_relative_to_last_close(self):
        high, low, close = make_ranging_series()
        # 마지막 값이 저점 부근이므로 120 수준은 저항이어야 합니다
        found = lv.swing_levels(high, low, close, pivot_window=3)
        top = max(found, key=lambda x: x.price)
        assert top.kind == "resistance"

    def test_repeated_touches_rank_higher(self):
        high, low, close = make_ranging_series(n_cycles=8)
        found = lv.swing_levels(high, low, close, pivot_window=3)
        assert max(x.touches for x in found) >= 3

    def test_selection_balances_support_and_resistance(self):
        """추세 종목에서도 양쪽이 나와야 합니다.

        터치 수만으로 자르면 하락 종목은 저항만 6개가 나와 '매수 기회 참고'라는
        목적을 못 채웁니다 (실측으로 확인한 문제).
        """
        high, low, close = make_ranging_series(levels=(100.0, 140.0), n_cycles=6)
        # 마지막을 중간값으로 만들어 위아래 모두 수준이 존재하게 합니다
        mid = pd.Series([120.0] * 8)
        close = pd.concat([close, mid], ignore_index=True)
        high = pd.concat([high, mid * 1.002], ignore_index=True)
        low = pd.concat([low, mid * 0.998], ignore_index=True)

        found = lv.swing_levels(high, low, close, pivot_window=3, max_levels=4)
        kinds = {x.kind for x in found}
        assert kinds == {"support", "resistance"}, f"한쪽만 나왔습니다: {kinds}"

    def test_results_are_sorted_by_price(self):
        high, low, close = make_ranging_series()
        found = lv.swing_levels(high, low, close, pivot_window=3)
        prices = [x.price for x in found]
        assert prices == sorted(prices)

    def test_too_short_series_returns_empty(self):
        s = pd.Series([1.0, 2.0, 3.0])
        assert lv.swing_levels(s, s, s) == []

    def test_random_walk_does_not_crash_and_returns_bounded(self):
        rng = np.random.default_rng(3)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
        found = lv.swing_levels(close * 1.01, close * 0.99, close, max_levels=6)
        assert len(found) <= 6


# ── API: 종목 상세의 수준·교차, 섹터 세분화, 관찰 목록 ───────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(path=tmp_path / "t.duckdb")
    panel = make_panel(n_days=400, n_tickers=12, seed=9)
    sectors = ["Technology", "Financial Services", "Healthcare"]
    industries = ["Semiconductors", "Banks - Diversified", "Biotechnology"]
    tick = sorted(panel["ticker"].unique())
    panel["sector"] = panel["ticker"].map({t: sectors[i % 3] for i, t in enumerate(tick)})
    panel["industry"] = panel["ticker"].map(
        {t: industries[i % 3] for i, t in enumerate(tick)}
    )
    panel["name"] = panel["ticker"] + " Inc"
    store.upsert_prices("US", panel)

    import app.store as store_mod

    monkeypatch.setattr(store_mod, "_store", store)
    from app.main import app

    return TestClient(app)


class TestStockDetailLevels:
    def test_detail_includes_levels_and_cross_interpretation(self, client):
        t = client.get("/api/universe/US").json()[0]["ticker"]
        body = client.get(f"/api/stocks/US/{t}").json()
        assert "levels" in body
        names = [i["indicator"] for i in body["interpretation"]]
        assert any("이동평균 교차" in n for n in names)
        # 근거 약한 지표에는 반드시 한계 문구가 있어야 합니다
        for item in body["interpretation"]:
            assert item["caveat"]

    def test_industry_is_exposed(self, client):
        t = client.get("/api/universe/US").json()[0]["ticker"]
        body = client.get(f"/api/stocks/US/{t}").json()
        assert body["industry"] in {
            "Semiconductors", "Banks - Diversified", "Biotechnology"
        }


class TestSectorLevels:
    def test_industry_level_returns_finer_groups(self, client):
        coarse = client.get("/api/sectors/US?level=sector").json()
        fine = client.get("/api/sectors/US?level=industry").json()
        assert {r["sector"] for r in coarse} == {
            "Technology", "Financial Services", "Healthcare"
        }
        assert {r["sector"] for r in fine} == {
            "Semiconductors", "Banks - Diversified", "Biotechnology"
        }

    def test_invalid_level_is_422(self, client):
        assert client.get("/api/sectors/US?level=nonsense").status_code == 422


class TestWatchlist:
    def test_watchlist_has_rules_and_says_what_score_is(self, client):
        """score 를 기대수익으로 읽으면 규칙 5개짜리 종목에 5배를 겁니다."""
        body = client.get("/api/watchlist/US").json()
        assert body["rules"], "규칙이 명시되지 않으면 근거 없는 목록이 됩니다"
        assert "score" in body["caveat"]
        assert "기대수익이 아니" in body["caveat"]
        assert body["as_of"]

    def test_candidates_carry_reasons(self, client):
        """이유 없는 후보는 없어야 합니다 -- score 는 곧 reasons 개수입니다."""
        body = client.get("/api/watchlist/US").json()
        for c in body["candidates"]:
            assert c["reasons"], f"{c['ticker']} 에 이유가 없습니다"
            assert c["score"] == len(c["reasons"])

    def test_trending_stock_is_picked_up(self, tmp_path, monkeypatch):
        """명백한 상승 추세 종목은 후보에 들어야 합니다 (탐지력 대조군)."""
        store = Store(path=tmp_path / "w.duckdb")
        flat = make_panel(n_days=300, n_tickers=5, seed=11)
        dates = sorted(flat["date"].unique())
        trend = pd.DataFrame(
            {
                "date": dates,
                "ticker": "WINNER",
                "open": np.linspace(100, 220, len(dates)),
                "close": np.linspace(100, 220, len(dates)),
                "high": np.linspace(101, 222, len(dates)),
                "low": np.linspace(99, 218, len(dates)),
                "volume": 1e6,
                "value": 1e9,
                "market_cap": 1e11,
                "shares": 1e6,
                "sector": "Technology",
                "industry": "Semiconductors",
                "name": "Winner Inc",
            }
        )
        store.upsert_prices("US", pd.concat([flat, trend], ignore_index=True))

        import app.store as store_mod

        monkeypatch.setattr(store_mod, "_store", store)
        from app.main import app

        body = TestClient(app).get("/api/watchlist/US").json()
        tickers = [c["ticker"] for c in body["candidates"]]
        assert "WINNER" in tickers
        winner = next(c for c in body["candidates"] if c["ticker"] == "WINNER")
        assert "정배열 (종가>20일선>60일선)" in winner["reasons"]

    def test_watchlist_404_without_data(self, client):
        assert client.get("/api/watchlist/KR").status_code == 404
