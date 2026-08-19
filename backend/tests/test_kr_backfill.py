"""한국 히스토리 백필.

배경 -- 실제 배포에서 난 문제:
    한국 데이터가 **19일치**만 쌓여 시장 화면·예측 화면이 통째로 비어 있었습니다.
    원인은 단순했습니다. 미국은 `years=10` 으로 받는데 한국은 `days=30` 이
    기본값이었고, CI 가 그 기본값을 그대로 썼습니다. 60일선·52주 고점·워크포워드
    예측은 전부 그보다 긴 기간을 요구하므로 줄줄이 빈칸이 됐습니다.

한국이 미국과 다른 이유는 소스의 구조입니다. KRX Open API 는 **하루치씩만**
주므로 10년치가 약 5,000회 요청입니다. 그래서 한 번에 다 받는 대신:

    이미 저장한 날은 건너뛰고, 병렬로 받고, 실행당 상한을 두고 과거로 넓힌다

이 테스트는 그 규칙들을 고정합니다. 네트워크는 쓰지 않고 provider 를 대역으로
바꿔 **어느 날짜를 요청했는지**를 검사합니다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.ingest import pipeline
from app.store import Store


class FakeProvider:
    """요청받은 날짜를 기록하고 합성 시세를 돌려주는 대역."""

    def __init__(self, *, fail_on: set[date] | None = None, empty_on: set[date] | None = None):
        self.asked: list[date] = []
        self.fail_on = fail_on or set()
        self.empty_on = empty_on or set()

    def fetch_daily_prices(self, trade_date: date) -> pd.DataFrame:
        self.asked.append(trade_date)
        if trade_date in self.fail_on:
            from app.providers.base import ProviderError

            raise ProviderError("모의 실패")
        if trade_date in self.empty_on:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "date": [trade_date, trade_date],
                "ticker": ["005930", "000660"],
                "name": ["삼성전자", "SK하이닉스"],
                "board": ["KOSPI", "KOSDAQ"],
                "open": [100.0, 200.0],
                "high": [101.0, 202.0],
                "low": [99.0, 198.0],
                "close": [100.5, 201.0],
                "volume": [1000.0, 2000.0],
                "value": [1e8, 2e8],
                "market_cap": [1e12, 5e11],
                "shares": [1e7, 1e7],
            }
        )


@pytest.fixture
def store(tmp_path):
    return Store(path=tmp_path / "kr.duckdb")


@pytest.fixture
def no_classification(monkeypatch):
    """업종 조회는 외부 호출이므로 끕니다 (별도 테스트에서 다룹니다)."""
    monkeypatch.setattr(
        pipeline, "fetch_kr_classification",
        lambda *a, **k: pd.DataFrame(columns=["ticker", "industry"]),
    )


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(
        "app.providers.krx_openapi.KrxOpenApiPriceProvider", lambda *a, **k: fake
    )
    return fake


class TestHistoryLength:
    def test_default_reaches_back_years_not_weeks(self, store, provider, no_classification):
        """기본값이 몇 주치면 장기 지표와 예측이 영원히 비어 있습니다."""
        pipeline.ingest_kr_prices(store=store, max_days_per_run=10_000)
        span = max(provider.asked) - min(provider.asked)
        assert span > timedelta(days=365 * 3), f"기간이 {span.days}일뿐입니다"

    def test_enough_for_walk_forward_in_one_cold_run(self, store, provider, no_classification):
        """캐시가 빈 첫 실행만으로도 예측이 가능한 길이를 확보해야 합니다.

        기준을 실측으로 잡았습니다 -- 학습 504 + 검증 126 + 예측구간 21 만
        계산하면 부족합니다. 12-1 모멘텀이 약 252거래일을 워밍업으로 먹기
        때문입니다:

            800거래일 -> 예측 불가(409) / 900거래일 -> 폴드 1 / 1,100 -> 폴드 3
        """
        pipeline.ingest_kr_prices(store=store)
        assert len(provider.asked) >= 900, (
            "이보다 짧으면 첫 실행에서 예측 화면이 계속 비어 있습니다"
        )

    def test_weekends_are_never_requested(self, store, provider, no_classification):
        pipeline.ingest_kr_prices(days=30, store=store)
        assert all(d.weekday() < 5 for d in provider.asked)

    def test_newest_first(self, store, provider, no_classification):
        """상한에 걸려 잘릴 때 최근 구간이 남아야 합니다 -- 화면이 먼저 쓰는 쪽입니다."""
        pipeline.ingest_kr_prices(store=store, max_days_per_run=5)
        assert provider.asked == sorted(provider.asked, reverse=True)


class TestIncremental:
    def test_already_stored_days_are_not_refetched(self, store, provider, no_classification):
        """과거 확정 시세는 불변입니다. 다시 받으면 요청 예산만 태웁니다."""
        pipeline.ingest_kr_prices(days=40, store=store)
        first = set(provider.asked)
        provider.asked.clear()

        pipeline.ingest_kr_prices(days=40, store=store)
        refetched = set(provider.asked) & first
        # 최근 며칠은 정정 가능성 때문에 의도적으로 다시 받습니다.
        assert len(refetched) <= 6, f"{len(refetched)}일을 불필요하게 다시 받았습니다"

    def test_recent_days_are_refetched_because_they_get_revised(
        self, store, provider, no_classification
    ):
        pipeline.ingest_kr_prices(days=40, store=store)
        provider.asked.clear()
        pipeline.ingest_kr_prices(days=40, store=store, refetch_recent_days=5)
        assert provider.asked, "최근 구간을 한 번도 다시 받지 않으면 정정이 반영되지 않습니다"

    def test_history_grows_across_runs(self, store, provider, no_classification):
        """한 번에 다 못 받으므로, 실행할 때마다 과거로 넓어져야 합니다."""
        pipeline.ingest_kr_prices(store=store, max_days_per_run=20)
        after_first = store.prices("KR")["date"].min()

        pipeline.ingest_kr_prices(store=store, max_days_per_run=20)
        after_second = store.prices("KR")["date"].min()
        assert after_second < after_first, "두 번째 실행이 과거를 더 받지 못했습니다"

    def test_remaining_history_is_reported(self, store, provider, no_classification):
        res = pipeline.ingest_kr_prices(store=store, max_days_per_run=5)
        assert any("남았" in w for w in res.warnings), (
            "아직 못 받은 구간이 있으면 화면이 그 사실을 알 수 있어야 합니다"
        )


class TestUniverseStability:
    def test_universe_is_fixed_after_the_first_run(self, store, provider, no_classification):
        """실행마다 상위 N을 다시 뽑으면 종목별 시계열에 구멍이 생깁니다."""
        pipeline.ingest_kr_prices(days=20, store=store, kospi_limit=1, kosdaq_limit=1)
        first = set(store.universe("KR")["ticker"])
        assert len(first) == 2, "보드마다 1종목씩"

        pipeline.ingest_kr_prices(days=60, store=store, kospi_limit=1, kosdaq_limit=1)
        assert set(store.universe("KR")["ticker"]) == first


class TestFailureHandling:
    def test_one_bad_day_does_not_lose_the_rest(self, store, monkeypatch, no_classification):
        fake = FakeProvider(fail_on={date.today() - timedelta(days=i) for i in range(3)})
        monkeypatch.setattr(
            "app.providers.krx_openapi.KrxOpenApiPriceProvider", lambda *a, **k: fake
        )
        res = pipeline.ingest_kr_prices(days=40, store=store)
        assert res.rows > 0

    def test_warnings_do_not_explode_into_hundreds_of_lines(
        self, store, monkeypatch, no_classification
    ):
        """전 구간이 실패해도 경고는 읽을 수 있는 분량이어야 합니다."""
        all_days = {date.today() - timedelta(days=i) for i in range(4000)}
        fake = FakeProvider(fail_on=all_days)
        monkeypatch.setattr(
            "app.providers.krx_openapi.KrxOpenApiPriceProvider", lambda *a, **k: fake
        )
        res = pipeline.ingest_kr_prices(store=store)
        assert res.rows == 0
        assert len(res.warnings) <= 8, f"경고가 {len(res.warnings)}줄입니다"

    def test_call_budget_stops_cleanly_and_says_so(self, store, monkeypatch, no_classification):
        """한도에 걸려도 받아둔 만큼은 저장되어야 합니다."""
        from app.providers.base import CallBudgetExceeded

        calls = {"n": 0}
        base = FakeProvider()

        class Budgeted(FakeProvider):
            def fetch_daily_prices(self, trade_date):
                calls["n"] += 1
                if calls["n"] > 10:
                    raise CallBudgetExceeded("한도 초과")
                return base.fetch_daily_prices(trade_date)

        monkeypatch.setattr(
            "app.providers.krx_openapi.KrxOpenApiPriceProvider", lambda *a, **k: Budgeted()
        )
        res = pipeline.ingest_kr_prices(days=200, store=store)
        assert res.rows > 0, "한도 전에 받은 데이터는 버리면 안 됩니다"
        assert any("한도" in w for w in res.warnings)


class TestClassificationResilience:
    def test_a_holiday_does_not_wipe_out_all_industries(self, monkeypatch):
        """한 날짜만 묻고 포기하면 공휴일 하나로 업종이 통째로 빕니다."""
        seen: list[str] = []

        class Resp:
            def __init__(self, rows):
                self._rows = rows

            def raise_for_status(self):
                pass

            def json(self):
                return {"OutBlock_1": self._rows}

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def post(self, url, data=None, **kw):
                seen.append(data["trdDd"])
                # 첫 날짜(휴장일)는 빈 응답, 그 이전 날짜는 정상
                if len(seen) <= 2:
                    return Resp([])
                return Resp([{"ISU_SRT_CD": "005930", "IDX_IND_NM": "반도체"}])

        monkeypatch.setattr("httpx.Client", lambda *a, **k: Client())
        out = pipeline.fetch_kr_classification(date(2026, 7, 31))
        assert not out.empty, "이전 영업일로 물러나 재시도해야 합니다"
        assert out.iloc[0]["industry"] == "반도체"
