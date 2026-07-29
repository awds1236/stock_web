"""시장 추상화 + 미국 내부자 지표 테스트.

특히 중요한 것: Form 4 집계가 **접수일**을 시점 축으로 쓰는가. 거래일로 집계하면
공시 전 정보를 쓰는 것이 되어 백테스트가 조용히 부풀려집니다.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest

from app import markets
from app.indicators import insider
from app.providers import sec_edgar


# ── 시장 추상화 ──────────────────────────────────────────────────────────
class TestMarkets:
    def test_korea_and_us_have_different_cost_structures(self):
        """한국 거래세와 미국 수수료는 자릿수가 다릅니다.

        공용 비용 모델을 쓰면 한쪽에 맞춘 값이 다른 쪽에 조용히 적용됩니다.
        """
        kr = markets.cost_model_for("KR")
        us = markets.cost_model_for("US")
        assert kr.sell_tax > us.sell_tax * 10, "한국 거래세가 미국 수수료보다 커야 합니다"

    def test_market_specific_env_override(self, monkeypatch):
        monkeypatch.setenv("COST_US_SLIPPAGE", "0.004")
        assert markets.cost_model_for("US").slippage == pytest.approx(0.004)
        # 다른 시장에는 영향이 없어야 합니다.
        assert markets.cost_model_for("KR").slippage == pytest.approx(
            markets.KR.costs.slippage
        )

    def test_env_override_does_not_leak_across_markets(self, monkeypatch):
        monkeypatch.setenv("COST_KR_SELL_TAX", "0.009")
        assert markets.cost_model_for("KR").sell_tax == pytest.approx(0.009)
        assert markets.cost_model_for("US").sell_tax == pytest.approx(
            markets.US.costs.sell_tax
        )

    def test_bad_env_value_fails_loudly(self, monkeypatch):
        """숫자가 아닌 세율을 조용히 무시하면 잘못된 비용으로 백테스트가 돕니다."""
        monkeypatch.setenv("COST_US_SELL_TAX", "삼십퍼센트")
        with pytest.raises(ValueError, match="COST_US_SELL_TAX"):
            markets.cost_model_for("US")

    def test_unknown_market_raises(self):
        with pytest.raises(ValueError, match="알 수 없는 시장"):
            markets.get_market("JP")

    def test_flow_kinds_differ_and_are_documented(self):
        """두 시장의 수급 데이터 성격이 다름이 코드에 명시되어 있어야 합니다."""
        assert markets.KR.flow_kind == "investor_type"
        assert markets.US.flow_kind == "insider"
        assert markets.KR.flow_caveat and markets.US.flow_caveat
        # 미국 쪽 한계 설명에 거래규모 제약 반론이 포함되어야 합니다.
        assert "H7" in markets.US.flow_caveat


# ── Form 4 파싱 ──────────────────────────────────────────────────────────
def make_form4(
    *,
    symbol: str = "ACME",
    code: str = "P",
    shares: str = "1000",
    price: str = "50.0",
    txn_date: str = "2026-07-20",
    is_officer: str = "1",
    is_director: str = "0",
    name: str = "DOE JANE",
) -> str:
    return f"""
    junk header text that EDGAR wraps around the xml
    <ownershipDocument>
      <issuer>
        <issuerCik>0000012345</issuerCik>
        <issuerTradingSymbol>{symbol}</issuerTradingSymbol>
      </issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
          <isOfficer>{is_officer}</isOfficer>
          <isDirector>{is_director}</isDirector>
          <isTenPercentOwner>0</isTenPercentOwner>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <transactionDate><value>{txn_date}</value></transactionDate>
          <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>{shares}</value></transactionShares>
            <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>
    trailing junk
    """


class TestParseForm4:
    def test_parses_open_market_purchase(self):
        txns = sec_edgar.parse_form4(make_form4(), date(2026, 7, 22))
        assert len(txns) == 1
        t = txns[0]
        assert t.ticker == "ACME"
        assert t.code == "P"
        assert t.value == pytest.approx(50_000.0)
        assert t.is_officer is True

    def test_keeps_filed_and_transaction_dates_separate(self):
        """이 둘을 혼동하면 공시 전 정보를 쓰게 됩니다."""
        txns = sec_edgar.parse_form4(
            make_form4(txn_date="2026-07-20"), date(2026, 7, 22)
        )
        assert txns[0].transaction_date == date(2026, 7, 20)
        assert txns[0].filed_date == date(2026, 7, 22)
        assert txns[0].filed_date > txns[0].transaction_date

    def test_ignores_option_grants_and_exercises(self):
        """수여(A)·옵션행사(M)를 '내부자 매수'로 세면 시그널이 오염됩니다."""
        for code in ("A", "M", "G", "F"):
            assert sec_edgar.parse_form4(make_form4(code=code), date(2026, 7, 22)) == []

    def test_ignores_zero_price_transactions(self):
        """가격 0은 공개시장 거래가 아닙니다."""
        assert sec_edgar.parse_form4(make_form4(price="0"), date(2026, 7, 22)) == []

    def test_captures_sales_separately(self):
        txns = sec_edgar.parse_form4(make_form4(code="S"), date(2026, 7, 22))
        assert len(txns) == 1 and txns[0].code == "S"

    def test_malformed_document_returns_empty_not_crash(self):
        assert sec_edgar.parse_form4("no xml here", date(2026, 7, 22)) == []
        assert sec_edgar.parse_form4("<ownershipDocument>broken", date(2026, 7, 22)) == []

    def test_client_rejects_missing_user_agent(self, monkeypatch):
        """SEC 는 연락처 없는 요청을 차단합니다. 실패는 호출 전에 나야 합니다."""
        monkeypatch.setattr(sec_edgar.settings, "sec_user_agent", "")
        with pytest.raises(Exception, match="User-Agent"):
            sec_edgar.SecEdgarClient()

    def test_client_rejects_user_agent_without_contact(self, monkeypatch):
        monkeypatch.setattr(sec_edgar.settings, "sec_user_agent", "my-scraper/1.0")
        with pytest.raises(Exception, match="User-Agent"):
            sec_edgar.SecEdgarClient()


# ── 내부자 지표 ──────────────────────────────────────────────────────────
def build_txns(rows: list[dict]) -> pd.DataFrame:
    txns = [
        sec_edgar.InsiderTransaction(
            filed_date=r["filed"],
            transaction_date=r.get("txn", r["filed"]),
            ticker=r["ticker"],
            issuer_cik="1",
            insider_name=r["name"],
            is_officer=r.get("officer", False),
            is_director=r.get("director", False),
            is_ten_pct_owner=False,
            code=r.get("code", "P"),
            shares=r["shares"],
            price=r["price"],
        )
        for r in rows
    ]
    return sec_edgar.transactions_to_frame(txns)


class TestDailyAggregate:
    def test_aggregates_by_filed_date_not_transaction_date(self):
        """시점 축이 접수일이어야 합니다 -- look-ahead 방지의 핵심.

        거래는 7/20에 일어났지만 공시는 7/22입니다. 7/20에 시그널이 잡히면
        아무도 모르는 정보로 매매한 것이 됩니다.
        """
        txns = build_txns(
            [{"filed": date(2026, 7, 22), "txn": date(2026, 7, 20),
              "ticker": "ACME", "name": "A", "shares": 100, "price": 10}]
        )
        agg = insider.daily_insider_aggregate(txns)
        assert agg["date"].tolist() == [pd.Timestamp("2026-07-22")]

    def test_nets_buys_against_sells(self):
        txns = build_txns(
            [
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "A",
                 "shares": 100, "price": 10, "code": "P"},
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "B",
                 "shares": 40, "price": 10, "code": "S"},
            ]
        )
        agg = insider.daily_insider_aggregate(txns)
        assert agg.loc[0, "buy_value"] == pytest.approx(1000.0)
        assert agg.loc[0, "sell_value"] == pytest.approx(400.0)
        assert agg.loc[0, "net_value"] == pytest.approx(600.0)

    def test_counts_distinct_buyers_not_filings(self):
        """한 사람이 여러 건으로 쪼개 신고해도 1명입니다.

        건수로 세면 분할 신고가 '클러스터 매수'로 둔갑합니다.
        """
        txns = build_txns(
            [
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "SAME",
                 "shares": 50, "price": 10},
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "SAME",
                 "shares": 50, "price": 10},
            ]
        )
        assert insider.daily_insider_aggregate(txns).loc[0, "n_buyers"] == 1

    def test_counts_officer_and_director_separately(self):
        txns = build_txns(
            [
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "CEO",
                 "shares": 10, "price": 10, "officer": True},
                {"filed": date(2026, 7, 22), "ticker": "ACME", "name": "DIR",
                 "shares": 10, "price": 10, "director": True},
            ]
        )
        agg = insider.daily_insider_aggregate(txns)
        assert agg.loc[0, "n_officer_buyers"] == 1
        assert agg.loc[0, "n_director_buyers"] == 1
        assert agg.loc[0, "n_buyers"] == 2

    def test_empty_input_returns_empty_frame_with_schema(self):
        agg = insider.daily_insider_aggregate(pd.DataFrame())
        assert agg.empty and "net_value" in agg.columns


class TestClusterBuyScore:
    def test_single_buyer_is_not_a_cluster(self):
        agg = pd.DataFrame(
            {
                "ticker": ["ACME"] * 3,
                "date": pd.date_range("2026-07-01", periods=3),
                "n_buyers": [1, 0, 0],
            }
        )
        assert insider.cluster_buy_score(agg, window=90, min_buyers=2).tolist() == [
            0.0, 0.0, 0.0
        ]

    def test_multiple_buyers_within_window_form_a_cluster(self):
        agg = pd.DataFrame(
            {
                "ticker": ["ACME"] * 3,
                "date": pd.date_range("2026-07-01", periods=3),
                "n_buyers": [1, 1, 1],
            }
        )
        scores = insider.cluster_buy_score(agg, window=90, min_buyers=2)
        assert scores.tolist() == [0.0, 2.0, 3.0]

    def test_does_not_mix_tickers(self):
        agg = pd.DataFrame(
            {
                "ticker": ["A", "B", "A", "B"],
                "date": pd.to_datetime(
                    ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-02"]
                ),
                "n_buyers": [1, 0, 1, 0],
            }
        )
        scores = insider.cluster_buy_score(agg, window=90, min_buyers=2)
        assert scores[agg["ticker"] == "B"].tolist() == [0.0, 0.0]


class TestBuyValueToMktcap:
    def test_normalizes_away_size(self):
        """대형주와 소형주의 같은 금액 매수를 구분해야 합니다."""
        buy = pd.Series([1e6, 1e6])
        cap = pd.Series([1e11, 1e9])
        ratio = insider.buy_value_to_mktcap(buy, cap)
        assert ratio.iloc[1] == pytest.approx(100 * ratio.iloc[0])

    def test_zero_mktcap_is_na_not_inf(self):
        assert insider.buy_value_to_mktcap(pd.Series([1e6]), pd.Series([0.0])).isna().all()


class TestDaysSinceLastBuy:
    def test_counts_up_after_a_buy(self):
        agg = pd.DataFrame(
            {
                "ticker": ["ACME"] * 4,
                "date": pd.date_range("2026-07-01", periods=4),
                "buy_value": [100.0, 0.0, 0.0, 50.0],
            }
        )
        assert insider.days_since_last_buy(agg).tolist() == [0.0, 1.0, 2.0, 0.0]

    def test_is_na_before_first_buy(self):
        agg = pd.DataFrame(
            {
                "ticker": ["ACME"] * 3,
                "date": pd.date_range("2026-07-01", periods=3),
                "buy_value": [0.0, 0.0, 10.0],
            }
        )
        result = insider.days_since_last_buy(agg)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2] == 0.0


# ── 엔진 재사용 확인 ─────────────────────────────────────────────────────
def test_engine_is_market_agnostic():
    """같은 백테스트 엔진이 미국 비용 모델로도 동작해야 합니다.

    엔진을 시장별로 포크하면 한쪽에만 버그 수정이 반영되는 사태가 옵니다.
    """
    from conftest import attach_signal, make_panel

    from app.backtest.engine import BacktestConfig, run_backtest

    panel = make_panel(n_days=200, n_tickers=15, seed=77)
    opens = panel.pivot_table(index="date", columns="ticker", values="open", aggfunc="last")
    sig = pd.DataFrame(
        __import__("numpy").random.default_rng(1).normal(size=opens.shape),
        index=opens.index,
        columns=opens.columns,
    )
    df = attach_signal(panel, sig, "sig")

    us = run_backtest(df, "sig", BacktestConfig(costs=markets.cost_model_for("US")))
    kr = run_backtest(df, "sig", BacktestConfig(costs=markets.cost_model_for("KR")))

    # 동일 시그널이면 한국 쪽 비용이 더 커야 합니다 (거래세 차이).
    assert kr.costs.sum() > us.costs.sum()
    assert us.meta["cost_model"]["sell_tax"] == pytest.approx(markets.US.costs.sell_tax)
    assert kr.meta["cost_model"]["sell_tax"] == pytest.approx(markets.KR.costs.sell_tax)


def test_env_isolation_between_tests():
    """monkeypatch 로 설정한 환경변수가 다른 테스트로 새지 않는지 확인."""
    assert os.getenv("COST_US_SLIPPAGE") is None
