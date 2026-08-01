"""섹터 집계의 **의미**를 고정하는 테스트.

이 두 함수는 섹터 화면·시장 분석·업종 분석이 모두 호출하는 공용 경로입니다.
그룹마다 파이썬 함수를 부르던 구현을 벡터화했는데(72초 -> 0.9초), 속도를 위해
값이 바뀌면 그건 개선이 아니라 조용한 버그입니다. 그래서 여기서는 빠른지가
아니라 **무엇을 계산하는지**를 못 박습니다.

특히 지키는 것:
    * 수익률이 전부 결측인 (일자 × 섹터) 그룹도 **행으로 남습니다.**
      각 종목의 첫 거래일이 여기 해당합니다. 빼버리면 출력 행 수가 달라져
      하위 계산(상대강도·breadth 정렬)이 어긋납니다.
    * n_constituents 는 그 날 **수익률을 계산할 수 있었던** 종목 수입니다
      (그 섹터에 속한 전체 종목 수가 아닙니다).
    * 가중치 합이 0이면 수익률은 정의되지 않습니다(NaN). 0으로 두면 '수익률
      0%'라는 실제와 다른 값이 됩니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.predict.sectors import aggregate_to_sector, sector_breadth


def panel_from(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    """(일자, 종목, 섹터, 종가) 목록으로 최소 패널을 만듭니다."""
    df = pd.DataFrame(rows, columns=["date", "ticker", "sector", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df["market_cap"] = 1.0
    return df


class TestAggregateToSector:
    def test_equal_weight_is_the_mean_of_member_returns(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),   # +10%
            ("2024-01-01", "B", "tech", 100.0),
            ("2024-01-02", "B", "tech", 130.0),   # +30%
        ])
        out = aggregate_to_sector(panel).set_index("date")
        assert out.loc["2024-01-02", "ret"] == pytest.approx(0.20)
        assert out.loc["2024-01-02", "n_constituents"] == 2

    def test_value_weight_follows_market_cap(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),   # +10%, 가중치 3
            ("2024-01-01", "B", "tech", 100.0),
            ("2024-01-02", "B", "tech", 130.0),   # +30%, 가중치 1
        ])
        panel.loc[panel["ticker"] == "A", "market_cap"] = 3.0
        out = aggregate_to_sector(panel, weight="value").set_index("date")
        # (0.1*3 + 0.3*1) / 4 = 0.15 -- 동일가중(0.20)과 달라야 합니다
        assert out.loc["2024-01-02", "ret"] == pytest.approx(0.15)

    def test_first_day_row_survives_with_zero_constituents(self):
        """첫 거래일은 수익률이 없지만 **행은 남아야** 합니다."""
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
        ])
        out = aggregate_to_sector(panel).set_index("date")
        assert len(out) == 2
        assert np.isnan(out.loc["2024-01-01", "ret"])
        assert out.loc["2024-01-01", "n_constituents"] == 0

    def test_zero_total_weight_is_undefined_not_zero(self):
        """가중치가 전부 0이면 '수익률 0%'가 아니라 '알 수 없음'입니다."""
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
        ])
        panel["market_cap"] = 0.0
        out = aggregate_to_sector(panel, weight="value").set_index("date")
        assert np.isnan(out.loc["2024-01-02", "ret"])

    def test_counts_only_tickers_with_a_computable_return(self):
        """그 날 상장·거래된 종목만 셉니다."""
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
            ("2024-01-02", "B", "tech", 100.0),   # B 는 이 날이 첫 거래일
        ])
        out = aggregate_to_sector(panel).set_index("date")
        assert out.loc["2024-01-02", "n_constituents"] == 1
        assert out.loc["2024-01-02", "ret"] == pytest.approx(0.10)

    def test_sectors_do_not_leak_into_each_other(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),   # tech +10%
            ("2024-01-01", "B", "bank", 100.0),
            ("2024-01-02", "B", "bank", 90.0),    # bank -10%
        ])
        out = aggregate_to_sector(panel)
        day2 = out[out["date"] == pd.Timestamp("2024-01-02")].set_index("sector")
        assert day2.loc["tech", "ret"] == pytest.approx(0.10)
        assert day2.loc["bank", "ret"] == pytest.approx(-0.10)

    def test_group_col_switches_the_grouping(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
        ])
        panel["industry"] = "semis"
        out = aggregate_to_sector(panel, group_col="industry")
        assert set(out["sector"]) == {"semis"}

    def test_rows_missing_a_sector_are_dropped(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
        ])
        panel.loc[panel["ticker"] == "A", "sector"] = None
        assert aggregate_to_sector(panel).empty


class TestSectorBreadth:
    def test_advancing_is_the_fraction_that_rose(self):
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),   # 상승
            ("2024-01-01", "B", "tech", 100.0),
            ("2024-01-02", "B", "tech", 90.0),    # 하락
            ("2024-01-01", "C", "tech", 100.0),
            ("2024-01-02", "C", "tech", 105.0),   # 상승
        ])
        out = sector_breadth(panel).set_index("date")
        assert out.loc["2024-01-02", "advancing"] == pytest.approx(2 / 3)
        assert out.loc["2024-01-02", "n"] == 3

    def test_flat_is_not_advancing(self):
        """보합은 상승이 아닙니다. 0을 상승으로 세면 breadth 가 부풀려집니다."""
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 100.0),
        ])
        out = sector_breadth(panel).set_index("date")
        assert out.loc["2024-01-02", "advancing"] == pytest.approx(0.0)

    def test_days_without_any_return_are_absent(self):
        """수익률이 없는 날은 breadth 를 정의할 수 없으므로 행이 없습니다."""
        panel = panel_from([
            ("2024-01-01", "A", "tech", 100.0),
            ("2024-01-02", "A", "tech", 110.0),
        ])
        out = sector_breadth(panel)
        assert list(out["date"]) == [pd.Timestamp("2024-01-02")]

    def test_empty_after_filtering_returns_empty_frame(self):
        panel = panel_from([("2024-01-01", "A", "tech", 100.0)])
        out = sector_breadth(panel)
        assert out.empty
        assert list(out.columns) == ["date", "sector", "advancing", "n"]


class TestPerformanceShape:
    """느려지면 화면이 아니라 **배포**가 먼저 무너집니다.

    CI 는 매 배포마다 이 집계를 종목·업종·시장 리포트에서 반복 호출합니다.
    그룹마다 파이썬 함수를 부르는 구현으로 되돌아가면 배포 시간이 분 단위로
    늘어나므로, 실측 규모에서 상한을 둡니다.
    """

    def test_ten_years_of_a_realistic_universe_is_fast(self):
        import time

        from conftest import make_panel

        panel = make_panel(n_days=1260, n_tickers=40, seed=7)
        tick = sorted(panel["ticker"].unique())
        panel["sector"] = panel["ticker"].map({t: f"s{i % 11}" for i, t in enumerate(tick)})

        start = time.perf_counter()
        aggregate_to_sector(panel)
        sector_breadth(panel)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, (
            f"섹터 집계가 {elapsed:.1f}초 걸립니다 -- 그룹마다 파이썬 함수를 "
            "부르는 구현으로 되돌아갔는지 확인하십시오"
        )
