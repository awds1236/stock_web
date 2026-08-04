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

from datetime import date, datetime, timedelta, timezone

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
        # 실제 provider 와 같은 모양: 코스피/코스닥 구분을 `market` 컬럼에 담아
        # 옵니다 (저장 스키마의 market 은 국가라 ingest 가 board 로 옮깁니다).
        return pd.DataFrame(
            {
                "date": [trade_date] * 4,
                "ticker": ["005930", "000660", "247540", "086520"],
                "name": ["삼성전자", "SK하이닉스", "에코프로비엠", "에코프로"],
                "market": ["KOSPI", "KOSPI", "KOSDAQ", "KOSDAQ"],
                "open": [100.0, 200.0, 50.0, 40.0],
                "high": [101.0, 202.0, 51.0, 41.0],
                "low": [99.0, 198.0, 49.0, 39.0],
                "close": [100.5, 201.0, 50.5, 40.5],
                "volume": [1000.0, 2000.0, 500.0, 400.0],
                "value": [1e8, 2e8, 5e7, 4e7],
                "market_cap": [1e12, 5e11, 3e11, 2e11],
                "shares": [1e7, 1e7, 1e7, 1e7],
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
        limits = {"KOSPI": 1, "KOSDAQ": 1}
        pipeline.ingest_kr_prices(days=20, store=store, board_limits=limits)
        first = set(store.universe("KR")["ticker"])
        assert len(first) == 2  # 시장마다 1종목씩

        pipeline.ingest_kr_prices(days=60, store=store, board_limits=limits)
        assert set(store.universe("KR")["ticker"]) == first


class TestBoardSplit:
    """코스피/코스닥을 나눠 담고, 시장별로 상한을 적용합니다.

    합쳐서 상위 N 을 뽑으면 코스닥이 거의 남지 않습니다 -- 코스닥 1위의 시총이
    코스피 100위권과 겹치는 정도이기 때문입니다. 그러면 "코스닥 분석"이라는
    화면이 성립하지 않습니다.
    """

    def test_board_is_stored_not_discarded(self, store, provider, no_classification):
        pipeline.ingest_kr_prices(days=20, store=store)
        universe = store.universe("KR")
        assert set(universe["board"]) == {"KOSPI", "KOSDAQ"}

    def test_market_column_stays_the_country_code(self, store, provider, no_classification):
        """provider 의 market(코스피/코스닥)이 저장 스키마의 market(국가)을
        덮어쓰면 시장 필터가 통째로 깨집니다."""
        pipeline.ingest_kr_prices(days=20, store=store)
        assert set(store.prices("KR")["market"]) == {"KR"}

    def test_limits_apply_per_board(self, store, provider, no_classification):
        pipeline.ingest_kr_prices(
            days=20, store=store, board_limits={"KOSPI": 1, "KOSDAQ": 2}
        )
        counts = store.universe("KR").groupby("board")["ticker"].nunique().to_dict()
        assert counts == {"KOSPI": 1, "KOSDAQ": 2}

    def test_warning_reports_the_split(self, store, provider, no_classification):
        """"300종목"만 알려주면 코스닥이 몇 개인지 알 수 없습니다."""
        res = pipeline.ingest_kr_prices(
            days=20, store=store, board_limits={"KOSPI": 1, "KOSDAQ": 1}
        )
        joined = " ".join(res.warnings)
        assert "KOSPI 1종목" in joined and "KOSDAQ 1종목" in joined

    def test_default_limits_are_200_and_100(self):
        assert pipeline.KR_BOARD_LIMITS == {"KOSPI": 200, "KOSDAQ": 100}
        assert sum(pipeline.KR_BOARD_LIMITS.values()) == 300


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


class TestLimitPerBoard:
    """`limit_per_board` 단위 검사 -- 물러서는 경로까지."""

    @staticmethod
    def _panel(rows):
        return pd.DataFrame(
            [
                {
                    "date": date(2025, 1, 2),
                    "ticker": t,
                    "board": b,
                    "market_cap": cap,
                    "value": cap / 100,
                    "close": 100.0,
                }
                for t, b, cap in rows
            ]
        )

    def test_keeps_the_largest_in_each_board(self):
        panel = self._panel([
            ("A", "KOSPI", 9e12), ("B", "KOSPI", 8e12), ("C", "KOSPI", 1e12),
            ("D", "KOSDAQ", 3e11), ("E", "KOSDAQ", 2e11),
        ])
        out, counts = pipeline.limit_per_board(panel, {"KOSPI": 2, "KOSDAQ": 1})
        assert set(out["ticker"]) == {"A", "B", "D"}
        assert counts == {"KOSPI": 2, "KOSDAQ": 1}

    def test_a_small_board_is_not_crowded_out_by_a_large_one(self):
        """이것이 시장별로 나눈 이유입니다. 합쳐서 자르면 코스닥이 사라집니다."""
        panel = self._panel(
            [(f"K{i}", "KOSPI", 1e13 - i) for i in range(10)]
            + [(f"Q{i}", "KOSDAQ", 1e10 - i) for i in range(10)]
        )
        merged, _ = pipeline.limit_to_top_market_cap(panel, 10)
        assert not any(t.startswith("Q") for t in merged["ticker"]), (
            "합산 상한에서는 코스닥이 전부 밀려납니다"
        )
        split, counts = pipeline.limit_per_board(panel, {"KOSPI": 5, "KOSDAQ": 5})
        assert counts == {"KOSPI": 5, "KOSDAQ": 5}

    def test_missing_board_falls_back_to_a_combined_cap(self):
        """구분을 못 하면 한쪽에 몰아주는 대신 합계 상한으로 물러섭니다."""
        panel = self._panel([("A", "KOSPI", 3.0), ("B", "KOSPI", 2.0)]).drop(
            columns=["board"]
        )
        out, counts = pipeline.limit_per_board(panel, {"KOSPI": 1, "KOSDAQ": 1})
        assert list(counts) == ["(구분 없음)"]
        assert out["ticker"].nunique() == 2

    def test_one_empty_board_reports_zero_instead_of_failing(self):
        panel = self._panel([("A", "KOSPI", 3.0), ("B", "KOSPI", 2.0)])
        out, counts = pipeline.limit_per_board(panel, {"KOSPI": 1, "KOSDAQ": 50})
        assert counts == {"KOSPI": 1, "KOSDAQ": 0}
        assert set(out["ticker"]) == {"A"}

    def test_empty_panel_is_handled(self):
        out, counts = pipeline.limit_per_board(pd.DataFrame())
        assert out.empty and counts == {}


class TestPublishLatency:
    """KRX 는 T일 데이터를 T+1일 08:00 KST 에 공개합니다.

    이걸 코드가 모르면 매 실행이 아직 없는 날짜를 물어 호출을 버리고, 무엇보다
    "왜 최신 일자가 어제인가"가 어디에도 적혀 있지 않게 됩니다. 실제로 배포된
    화면의 한국 최신 일자가 하루 뒤처져 보인다는 보고가 있었습니다.
    """

    KST = timezone(timedelta(hours=9))

    def at(self, y, m, d, hh, mm=0):
        return datetime(y, m, d, hh, mm, tzinfo=self.KST)

    def test_right_after_the_close_today_is_not_available_yet(self):
        """금요일 16:00 KST -- 장은 끝났지만 그날 데이터는 아직 없습니다."""
        assert pipeline.kr_latest_available(self.at(2026, 7, 31, 16)) == date(2026, 7, 30)

    def test_before_eight_am_still_lags_one_more_day(self):
        assert pipeline.kr_latest_available(self.at(2026, 8, 1, 7, 30)) == date(2026, 7, 30)

    def test_after_eight_am_the_previous_session_appears(self):
        assert pipeline.kr_latest_available(self.at(2026, 8, 1, 8, 30)) == date(2026, 7, 31)

    def test_weekend_walks_back_to_a_weekday(self):
        """일요일 아침에 물으면 토요일이 아니라 금요일이 상한이어야 합니다."""
        got = pipeline.kr_latest_available(self.at(2026, 8, 2, 9))
        assert got == date(2026, 7, 31) and got.weekday() < 5

    def test_ingest_does_not_ask_for_dates_that_cannot_exist(self, store, provider,
                                                             no_classification):
        pipeline.ingest_kr_prices(days=10, store=store)
        assert max(provider.asked) <= pipeline.kr_latest_available(), (
            "존재할 수 없는 날짜를 물으면 호출 한도만 소모합니다"
        )
