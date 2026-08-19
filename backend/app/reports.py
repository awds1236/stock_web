"""분석 리포트 -- **숫자를 만드는 유일한 자리**.

이 모듈이 존재하는 이유는 하나입니다:

    화면에 보여주는 숫자와 AI 에게 넘기는 숫자가 **같아야** 합니다.

AI 프롬프트를 따로 만들면 두 경로가 조용히 갈라집니다. 화면에는 RSI 62 가
찍혀 있는데 AI 는 다른 구간의 RSI 를 근거로 서술하는 상황이 되고, 그건
사용자가 발견할 방법이 없는 종류의 오류입니다. 그래서 API 응답과 AI 프롬프트는
**같은 dict** 를 씁니다.

또 하나의 원칙: 이 모듈은 **계산만** 합니다. 매수/매도 판단을 내리지 않고,
"규칙에 걸렸다"는 사실과 그 규칙의 한계를 함께 돌려줍니다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.indicators import levels as lv
from app.indicators import price as px
from app.predict import sectors as sec
from app.store import Store, get_store

# 종목 스크리닝 규칙. 근거를 숨긴 "추천"은 이 앱의 원칙(검증 없는 신호 금지)과
# 충돌하므로, 어떤 규칙에 걸렸는지를 응답에 그대로 노출합니다.
WATCH_RULES = [
    "추세: 종가 > 20일선 > 60일선 (정배열)",
    "최근 골든크로스: 20/60일선이 최근 15거래일 내 상향 교차",
    "52주 고가 근접: 고점 대비 -5% 이내 (모멘텀 대용치, 문헌 근거 있음)",
    "상대강도: 최근 60일 수익률이 유니버스 평균 초과",
    "거래대금 급증: 최근 5일 평균이 60일 평균의 1.5배 이상",
]

RULE_CAVEAT = (
    "이 규칙 조합의 예측력은 검증되지 않았습니다. 모멘텀 계열이라는 공통점만 "
    "문헌 근거가 있으며, 걸린 규칙 수(score)는 신뢰도가 아니라 '몇 개에 "
    "걸렸는가'일 뿐입니다."
)


# 패널 캐시.
#
# 모든 리포트가 유니버스 전체 패널을 필요로 합니다 -- 상대강도와 순위는 다른
# 종목 없이는 계산할 수 없기 때문입니다. 종목 하나를 볼 때마다 수십만 행을
# DuckDB 에서 다시 읽어 DataFrame 으로 만들면, "버튼 눌러 이 종목만 빠르게"가
# 성립하지 않습니다.
#
# 무효화는 데이터 지문(마지막 일자 + 행 수)으로 합니다. 캐시된 프레임은
# 읽기 전용으로만 쓰이며(모든 호출자가 필터링/copy 로 시작), 여기서 변형하지
# 않습니다.
_PANEL_CACHE: dict[str, tuple[tuple, pd.DataFrame]] = {}


def load_panel(market: str, store: Store) -> pd.DataFrame:
    stamp = store.stamp(market)
    hit = _PANEL_CACHE.get(market)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    panel = store.prices(market)
    _PANEL_CACHE[market] = (stamp, panel)
    return panel


def clear_panel_cache() -> None:
    _PANEL_CACHE.clear()


def _split_ends(ranked: list, k: int) -> tuple[list, list]:
    """정렬된 목록의 양 끝을 **겹치지 않게** 잘라냅니다.

    단순히 `[:k]` 와 `[-k:]` 를 쓰면 항목이 k개 이하일 때 같은 종목이 '상위'와
    '하위' 양쪽에 나옵니다. 실제로 구성종목 2개짜리 업종에서 두 표가 똑같은
    두 종목을 보여주는 화면이 나왔습니다 -- 사용자에게는 데이터 오류로 보입니다.
    나눌 것이 없으면 하위는 비워두고, 화면이 그 사실을 말합니다.
    """
    head = ranked[:k]
    tail = ranked[max(k, len(ranked) - k) :]
    return head, tail[::-1]


# ── 종목 ──────────────────────────────────────────────────────────────────
def stock_report(market: str, ticker: str, *, store: Store | None = None) -> dict[str, Any]:
    """종목 하나에 대한 전체 분석 묶음.

    **종목 단위로만 계산합니다.** 유니버스 전체를 한 번에 돌리지 않는 이유는
    속도입니다 -- 300종목 전부를 분석하면 수십 초가 걸리고, 사용자는 지금 보고
    있는 한 종목의 결과만 필요합니다. 유니버스 비교가 필요한 항목(상대강도,
    순위)만 최소한의 집계를 추가로 돕니다.
    """
    store = store or get_store()
    market = market.upper()
    panel = load_panel(market, store)
    if panel.empty:
        raise LookupError(f"{market} 데이터가 없습니다. 먼저 수집하십시오.")

    g = panel[panel["ticker"] == ticker].sort_values("date").reset_index(drop=True)
    if g.empty:
        raise LookupError(f"{market}/{ticker} 데이터가 없습니다. 먼저 수집하십시오.")

    close = g["close"]
    as_of = pd.Timestamp(g["date"].iloc[-1]).date()

    ind = {
        "rsi_14": _last(px.rsi(close, 14)),
        "sma_20": _last(px.sma(close, 20)),
        "sma_60": _last(px.sma(close, 60)),
        "sma_200": _last(px.sma(close, 200)),
        "macd_hist": _last(px.macd(close)["hist"]),
        "vol_20d": _last(px.realized_volatility(close, 20)),
        "vol_60d": _last(px.realized_volatility(close, 60)),
        "pct_from_52w_high": _last(px.pct_from_52w_high(close)),
    }
    bb = px.bollinger(close)
    ind["bollinger_upper"] = _last(bb["upper"])
    ind["bollinger_lower"] = _last(bb["lower"])

    last_close = _f(close.iloc[-1])
    swing = lv.swing_levels(g["high"], g["low"], close)
    cross_20_60 = lv.ma_cross(close, fast=20, slow=60)
    cross_50_200 = lv.ma_cross(close, fast=50, slow=200)

    # 유니버스 비교값. 종목 하나만 보면 "20% 올랐다"가 좋은 건지 알 수 없습니다.
    peers = universe_returns(panel, window=60)
    peer_20 = universe_returns(panel, window=20)
    r60 = _ret(close, 60)
    rank = None
    if r60 is not None and peers:
        better = sum(1 for v in peers.values() if v > r60)
        rank = {"rank": better + 1, "of": len(peers)}

    matched = matched_rules(g, universe_mean_ret60=_mean(peers))

    sector_name = _first_valid(g, "sector")
    industry_name = _first_valid(g, "industry")
    sector_ret_20d = _group_return(panel, sector_name, level="sector", window=20)

    return {
        "market": market,
        "ticker": ticker,
        "name": _first_valid(g, "name"),
        "sector": sector_name,
        "industry": industry_name,
        "as_of": str(as_of),
        "price": {
            "close": last_close,
            "prev_close": _f(close.iloc[-2]) if len(close) >= 2 else None,
            "change_1d": _ret(close, 1),
            "high_52w": _f(close.tail(252).max()),
            "low_52w": _f(close.tail(252).min()),
        },
        "returns": {
            "1d": _ret(close, 1),
            "5d": _ret(close, 5),
            "20d": _ret(close, 20),
            "60d": _ret(close, 60),
            "120d": _ret(close, 120),
            "252d": _ret(close, 252),
        },
        "indicators": ind,
        "trend": {
            "stack": _stack_state(last_close, ind["sma_20"], ind["sma_60"]),
            "cross_20_60": _cross_dict(cross_20_60),
            "cross_50_200": _cross_dict(cross_50_200),
        },
        "liquidity": _liquidity(g),
        "levels": [
            {
                "price": round(x.price, 4),
                "kind": x.kind,
                "touches": x.touches,
                "distance_pct": round(x.distance_pct, 4),
            }
            for x in swing
        ],
        "interpretation": interpret_indicators(g)
        + interpret_levels(swing, cross_20_60, cross_50_200),
        "rules": {
            "matched": matched,
            "score": len(matched),
            "all": WATCH_RULES,
            "caveat": RULE_CAVEAT,
        },
        "relative": {
            "sector": sector_name,
            "sector_ret_20d": sector_ret_20d,
            "stock_ret_20d": _ret(close, 20),
            "excess_vs_sector_20d": _diff(_ret(close, 20), sector_ret_20d),
            "universe_ret_20d": _mean(peer_20),
            "excess_vs_universe_20d": _diff(_ret(close, 20), _mean(peer_20)),
            "rank_60d": rank,
        },
        "data_quality": {
            "n_days": int(len(g)),
            "first_date": str(pd.Timestamp(g["date"].iloc[0]).date()),
            "last_date": str(as_of),
            "n_universe": int(panel["ticker"].nunique()),
        },
        "caveats": [
            "지표는 상태 서술이지 매매 신호가 아닙니다.",
            RULE_CAVEAT,
            "유니버스는 현재 시가총액 상위 종목으로 제한되어 있어 상대순위에 "
            "생존편향이 있습니다.",
        ],
    }


def matched_rules(g: pd.DataFrame, universe_mean_ret60: float | None) -> list[str]:
    """WATCH_RULES 중 이 종목에 걸린 것들."""
    c = g["close"].reset_index(drop=True)
    if len(c) < 70:
        return []

    matched: list[str] = []
    sma20 = c.rolling(20).mean().iloc[-1]
    sma60 = c.rolling(60).mean().iloc[-1]
    if np.isfinite(sma20) and np.isfinite(sma60) and c.iloc[-1] > sma20 > sma60:
        matched.append("정배열 (종가>20일선>60일선)")

    if lv.recent_cross_tag(c, fast=20, slow=60, within_days=15) == "golden":
        matched.append("최근 골든크로스 (20/60)")

    hi52 = px.pct_from_52w_high(c).iloc[-1]
    if np.isfinite(hi52) and hi52 > -0.05:
        matched.append("52주 고가 -5% 이내")

    r60 = _ret(c, 60)
    if r60 is not None and universe_mean_ret60 is not None and r60 > universe_mean_ret60:
        matched.append("60일 상대강도 우위")

    if "value" in g.columns and g["value"].notna().sum() >= 60:
        v5, v60 = g["value"].tail(5).mean(), g["value"].tail(60).mean()
        if np.isfinite(v5) and np.isfinite(v60) and v60 > 0 and v5 / v60 >= 1.5:
            matched.append("거래대금 급증 (5일/60일 ≥1.5배)")
    return matched


def _liquidity(g: pd.DataFrame) -> dict[str, float | None]:
    if "value" not in g.columns or g["value"].notna().sum() < 5:
        return {"value_5d": None, "value_60d": None, "surge_ratio": None}
    v5 = _f(g["value"].tail(5).mean())
    v60 = _f(g["value"].tail(60).mean())
    return {
        "value_5d": v5,
        "value_60d": v60,
        "surge_ratio": _f(v5 / v60) if v5 is not None and v60 else None,
    }


# ── 섹터 ──────────────────────────────────────────────────────────────────
def sector_report(
    market: str,
    sector: str,
    *,
    level: str = "industry",
    store: Store | None = None,
) -> dict[str, Any]:
    """섹터(또는 세부업종) 하나에 대한 분석 묶음.

    섹터를 1급으로 다루는 이유: 섹터·국가 단위 아웃오브샘플 R² (0.29~0.95%)가
    개별종목(0.33~0.40%)과 동등하거나 더 높다는 문헌 근거가 있습니다.
    """
    store = store or get_store()
    market = market.upper()
    panel = load_panel(market, store)
    if panel.empty:
        raise LookupError(f"{market} 데이터가 없습니다. 먼저 수집하십시오.")
    if level not in panel.columns or panel[level].isna().all():
        raise LookupError(f"{market} 의 업종 정보가 없습니다. 데이터를 다시 수집하십시오.")

    members = panel[panel[level] == sector]
    if members.empty:
        raise LookupError(f"{market} 에 '{sector}' 업종이 없습니다.")

    agg = sec.aggregate_to_sector(panel, group_col=level)
    rs = sec.relative_strength(agg, window=60)
    breadth = sec.sector_breadth(panel, group_col=level)
    mine = rs[rs["sector"] == sector].sort_values("date")
    my_breadth = breadth[breadth["sector"] == sector].sort_values("date")

    # 구성종목 성과. 리더·부진 종목을 함께 줘야 "업종이 오른 게 소수 종목
    # 때문인가"를 판단할 수 있습니다.
    per_ticker = _per_ticker_returns(members)
    ranked = sorted(
        (x for x in per_ticker if x["ret_20d"] is not None),
        key=lambda x: x["ret_20d"],
        reverse=True,
    )

    all_sectors = _sector_table(rs, breadth)
    my_rank = next(
        (i + 1 for i, s in enumerate(all_sectors) if s["sector"] == sector), None
    )
    leaders, laggards = _split_ends(ranked, 5)

    return {
        "market": market,
        "level": level,
        "sector": sector,
        "as_of": str(pd.Timestamp(panel["date"].max()).date()),
        "ret_20d": _cum(mine["ret"].tail(20)),
        "ret_60d": _cum(mine["ret"].tail(60)),
        "relative_strength_60d": _last(mine["rs_60"]) if "rs_60" in mine else None,
        "breadth": _f(my_breadth["advancing"].iloc[-1]) if len(my_breadth) else None,
        "n_constituents": int(members["ticker"].nunique()),
        "rank_by_ret_20d": my_rank,
        "n_sectors": len(all_sectors),
        "leaders": leaders,
        "laggards": laggards,
        "market_ret_20d": _mean(universe_returns(panel, window=20)),
        "peer_sectors": all_sectors[:8],
        "caveats": [
            "업종 분류는 **현재 시점** 분류를 과거 구간에도 적용한 것입니다. "
            "업종 재분류가 있었다면 과거 집계가 실제와 다릅니다.",
            "breadth(상승 종목 비율)가 0.5 미만이면 업종 수익률이 소수 종목에 "
            "끌려간 것이므로 업종 전체의 강세로 읽으면 안 됩니다.",
        ],
    }


def _sector_table(rs: pd.DataFrame, breadth: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for name, group in rs.groupby("sector"):
        g = group.sort_values("date")
        b = breadth[breadth["sector"] == name].sort_values("date")
        rows.append(
            {
                "sector": str(name),
                "ret_20d": _cum(g["ret"].tail(20)),
                "ret_60d": _cum(g["ret"].tail(60)),
                "relative_strength_60d": _last(g["rs_60"]) if "rs_60" in g else None,
                "breadth": _f(b["advancing"].iloc[-1]) if len(b) else None,
                "n_constituents": int(g["n_constituents"].iloc[-1]) if len(g) else 0,
            }
        )
    return sorted(rows, key=lambda r: r["ret_20d"] if r["ret_20d"] is not None else -9e9,
                  reverse=True)


# ── 시장 전체 ─────────────────────────────────────────────────────────────
def market_report(market: str, *, store: Store | None = None) -> dict[str, Any]:
    """시장 전체 현황 + 주목할 종목 후보.

    지수를 쓰지 않고 **유니버스 동일가중 평균**으로 시장을 대신합니다. 이유는
    정직함입니다 -- 이 앱이 가진 데이터는 시총 상위 N종목이지 지수가 아니고,
    그것을 "코스피"라고 부르면 사용자가 지수 수익률과 대조하다 혼란에 빠집니다.
    """
    store = store or get_store()
    market = market.upper()
    panel = load_panel(market, store)
    if panel.empty:
        raise LookupError(f"{market} 데이터가 없습니다. 먼저 수집하십시오.")

    as_of = pd.Timestamp(panel["date"].max()).date()
    per_ticker = _per_ticker_returns(panel)
    ranked_20 = sorted(
        (x for x in per_ticker if x["ret_20d"] is not None),
        key=lambda x: x["ret_20d"],
        reverse=True,
    )

    level = (
        "industry"
        if "industry" in panel.columns and panel["industry"].notna().any()
        else "sector"
    )
    sectors: list[dict] = []
    if level in panel.columns and panel[level].notna().any():
        agg = sec.aggregate_to_sector(panel, group_col=level)
        rs = sec.relative_strength(agg, window=60)
        sectors = _sector_table(rs, sec.sector_breadth(panel, group_col=level))

    stats = _market_internals(panel)
    attention = _attention_list(panel, per_ticker)
    sectors_top, sectors_bottom = _split_ends(sectors, 5)
    movers_up, movers_down = _split_ends(ranked_20, 8)

    return {
        "market": market,
        "as_of": str(as_of),
        "universe": {
            "n_tickers": int(panel["ticker"].nunique()),
            "note": "지수가 아니라 수집된 시총 상위 종목의 동일가중 평균입니다.",
        },
        "index_proxy": {
            "ret_1d": _mean(universe_returns(panel, window=1)),
            "ret_5d": _mean(universe_returns(panel, window=5)),
            "ret_20d": _mean(universe_returns(panel, window=20)),
            "ret_60d": _mean(universe_returns(panel, window=60)),
            "ret_120d": _mean(universe_returns(panel, window=120)),
        },
        "internals": stats,
        "sectors_top": sectors_top,
        "sectors_bottom": sectors_bottom,
        "level": level,
        "movers_up": movers_up,
        "movers_down": movers_down,
        "attention": attention,
        "rules": WATCH_RULES,
        "caveats": [
            "여기서 '시장'은 수집된 유니버스의 동일가중 평균이며 공식 지수가 "
            "아닙니다. 지수와 수치가 다른 것이 정상입니다.",
            RULE_CAVEAT,
            "주목 종목은 매수 추천이 아니라 규칙에 걸린 관찰 후보입니다.",
        ],
    }


def _market_internals(panel: pd.DataFrame) -> dict[str, float | None]:
    """시장 내부지표. 평균 수익률만으로는 '몇 종목이 끌었는가'를 알 수 없습니다."""
    above_sma60 = 0
    near_high = 0
    counted = 0
    vols: list[float] = []
    for _, g in panel.groupby("ticker", sort=False):
        c = g.sort_values("date")["close"].reset_index(drop=True)
        if len(c) < 60:
            continue
        counted += 1
        sma60 = c.rolling(60).mean().iloc[-1]
        if np.isfinite(sma60) and c.iloc[-1] > sma60:
            above_sma60 += 1
        hi = px.pct_from_52w_high(c).iloc[-1]
        if np.isfinite(hi) and hi > -0.05:
            near_high += 1
        v = px.realized_volatility(c, 20).iloc[-1]
        if np.isfinite(v):
            vols.append(float(v))

    if not counted:
        return {"above_sma60_pct": None, "near_52w_high_pct": None,
                "median_vol_20d": None, "n_evaluated": 0}
    return {
        "above_sma60_pct": round(above_sma60 / counted, 4),
        "near_52w_high_pct": round(near_high / counted, 4),
        "median_vol_20d": _f(np.median(vols)) if vols else None,
        "n_evaluated": counted,
    }


def _attention_list(panel: pd.DataFrame, per_ticker: list[dict]) -> list[dict]:
    """규칙에 걸린 종목을 점수순으로. 각 종목에 **어떤 규칙에 걸렸는지**를 함께."""
    peers = universe_returns(panel, window=60)
    mean_ret60 = _mean(peers)
    ret20_by_ticker = {x["ticker"]: x["ret_20d"] for x in per_ticker}

    out: list[dict] = []
    for t, g in panel.groupby("ticker", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        matched = matched_rules(g, mean_ret60)
        if not matched:
            continue
        out.append(
            {
                "ticker": t,
                "name": _first_valid(g, "name"),
                "sector": _first_valid(g, "sector"),
                "industry": _first_valid(g, "industry"),
                # 화면이 이 종목의 현재가를 조회할 때 심볼 접미사를 고르는 데
                # 씁니다 (한국: KOSPI -> .KS, KOSDAQ -> .KQ).
                "board": _first_valid(g, "board"),
                "close": _f(g["close"].iloc[-1]),
                "ret_20d": ret20_by_ticker.get(t),
                "pct_from_52w_high": _f(px.pct_from_52w_high(g["close"]).iloc[-1]),
                "score": len(matched),
                "reasons": matched,
            }
        )
    out.sort(key=lambda x: (-x["score"], -(x["ret_20d"] if x["ret_20d"] is not None else -9e9)))
    return out[:12]


# ── 해석 카드 (화면·AI 공용) ──────────────────────────────────────────────
def interpret_indicators(g: pd.DataFrame) -> list[dict]:
    """지표 해석 -- **매매 신호가 아니라 상태 서술**입니다.

    'RSI 30 이하이므로 매수' 같은 문구를 내지 않습니다. 문헌상 단일 기술적
    지표의 예측력은 약하고, 특히 RSI 는 추세장에서 과매수 구간에 머문 채 계속
    오르기 때문입니다. 각 항목에 한계를 함께 실어 보냅니다.
    """
    close = g["close"]
    out: list[dict] = []

    rsi = px.rsi(close, 14).dropna()
    if len(rsi):
        v = float(rsi.iloc[-1])
        state = "과매수 구간" if v >= 70 else ("과매도 구간" if v <= 30 else "중립 구간")
        out.append({
            "indicator": "RSI(14)",
            "value": round(v, 1),
            "state": state,
            "reading": f"현재 RSI 는 {v:.1f} 로 {state}입니다.",
            "caveat": "추세장에서는 과매수 구간에 머문 채 계속 오릅니다. "
                      "단독 매매 신호로 쓰면 상승장에서 조기 매도를 반복합니다.",
        })

    sma20, sma60 = px.sma(close, 20).dropna(), px.sma(close, 60).dropna()
    if len(sma20) and len(sma60):
        above = float(sma20.iloc[-1]) > float(sma60.iloc[-1])
        out.append({
            "indicator": "이동평균 20/60",
            "value": round(float(sma20.iloc[-1]) / float(sma60.iloc[-1]) - 1, 4),
            "state": "단기 우위" if above else "장기 우위",
            "reading": f"20일선이 60일선 {'위' if above else '아래'}에 있습니다.",
            "caveat": "이동평균은 구조적으로 가격을 뒤따릅니다. 추세 판별 필터로는 "
                      "쓸모가 있으나 진입 타이밍 근거로는 약합니다.",
        })

    vol = px.realized_volatility(close, 20).dropna()
    if len(vol):
        v = float(vol.iloc[-1])
        out.append({
            "indicator": "실현변동성(20일, 연율)",
            "value": round(v, 4),
            "state": "높음" if v > 0.4 else ("낮음" if v < 0.2 else "보통"),
            "reading": f"연율 환산 변동성은 {v * 100:.1f}% 입니다.",
            "caveat": "변동성은 방향이 아니라 폭입니다. 다만 수익률 방향보다 "
                      "예측 가능성이 훨씬 높아, 포지션 크기 결정에 유용합니다.",
        })

    hi = px.pct_from_52w_high(close).dropna()
    if len(hi):
        v = float(hi.iloc[-1])
        out.append({
            "indicator": "52주 고점 대비",
            "value": round(v, 4),
            "state": "신고가 부근" if v > -0.05 else "고점 대비 하락",
            "reading": f"52주 고점 대비 {v * 100:.1f}% 위치입니다.",
            "caveat": "모멘텀 대용치로 문헌 근거가 있는 편이지만, 국내 시장의 "
                      "모멘텀 효과는 미국 대비 약하다는 연구가 있습니다.",
        })
    return out


def interpret_levels(swing, c1: lv.CrossState, c2: lv.CrossState) -> list[dict]:
    """지지/저항·이동평균 교차 해석 카드.

    이 둘은 이 앱에서 근거가 가장 약한 지표입니다. 해석 문구보다 한계 문구가
    더 중요합니다 -- '골든크로스가 떴으니 매수' 로 읽히지 않게 씁니다.
    """
    out: list[dict] = []

    supports = [x for x in swing if x.kind == "support"]
    resistances = [x for x in swing if x.kind == "resistance"]
    if supports or resistances:
        near_s = max(supports, key=lambda x: x.price) if supports else None
        near_r = min(resistances, key=lambda x: x.price) if resistances else None
        parts = []
        if near_s:
            parts.append(
                f"가까운 지지 후보 {near_s.price:,.0f} ({near_s.distance_pct * 100:+.1f}%, "
                f"터치 {near_s.touches}회)"
            )
        if near_r:
            parts.append(
                f"가까운 저항 후보 {near_r.price:,.0f} ({near_r.distance_pct * 100:+.1f}%, "
                f"터치 {near_r.touches}회)"
            )
        out.append({
            "indicator": "지지/저항 (스윙 클러스터)",
            "value": round(near_s.price if near_s else near_r.price, 2),
            "state": f"수준 {len(swing)}개 감지",
            "reading": ". ".join(parts) + ".",
            "caveat": "지지/저항은 학술 근거가 약한 참고선입니다. 많은 참여자가 "
                      "의식하는 가격대라는 자기실현 가설이 논리의 전부이며, 뚫리면 "
                      "의미가 반전됩니다. 매매 신호가 아니라 차트 참고용입니다.",
        })

    for c, label in ((c1, "20/60일"), (c2, "50/200일")):
        if c.state == "insufficient":
            continue
        state_kr = "정배열 (골든)" if c.state == "golden" else "역배열 (데드)"
        if c.last_cross and c.days_since_cross is not None:
            cross_kr = "골든크로스" if c.last_cross == "golden" else "데드크로스"
            reading = (
                f"현재 {state_kr}. 마지막 교차는 {c.days_since_cross}거래일 전 "
                f"{cross_kr}입니다."
            )
        else:
            reading = f"현재 {state_kr}. 표시 구간 내 교차 없음."
        out.append({
            "indicator": f"이동평균 교차 ({label})",
            "value": 1.0 if c.state == "golden" else -1.0,
            "state": state_kr,
            "reading": reading,
            "caveat": "이동평균 교차는 구조적으로 후행 신호이며, 단독 사용 성과에 "
                      "대한 문헌 근거는 혼재합니다. 추세 확인용 서술이지 진입 "
                      "신호가 아닙니다.",
        })
    return out


# ── 유틸 ──────────────────────────────────────────────────────────────────
def universe_returns(panel: pd.DataFrame, window: int) -> dict[str, float]:
    """종목별 `window` 거래일 수익률. 유니버스 평균·순위의 재료입니다."""
    out: dict[str, float] = {}
    for t, g in panel.groupby("ticker", sort=False):
        c = g.sort_values("date")["close"].reset_index(drop=True)
        r = _ret(c, window)
        if r is not None:
            out[t] = r
    return out


def _per_ticker_returns(panel: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for t, g in panel.groupby("ticker", sort=False):
        g = g.sort_values("date")
        c = g["close"].reset_index(drop=True)
        out.append(
            {
                "ticker": t,
                "name": _first_valid(g, "name"),
                "sector": _first_valid(g, "sector"),
                "industry": _first_valid(g, "industry"),
                "close": _f(c.iloc[-1]) if len(c) else None,
                "ret_5d": _ret(c, 5),
                "ret_20d": _ret(c, 20),
                "ret_60d": _ret(c, 60),
            }
        )
    return out


def _group_return(
    panel: pd.DataFrame, name: str | None, *, level: str, window: int
) -> float | None:
    if not name or level not in panel.columns:
        return None
    members = panel[panel[level] == name]
    if members.empty:
        return None
    return _mean(universe_returns(members, window))


def _ret(close: pd.Series, days: int) -> float | None:
    c = close.dropna().reset_index(drop=True)
    if len(c) <= days:
        return None
    base = float(c.iloc[-1 - days])
    if not np.isfinite(base) or base <= 0:
        return None
    return _f(float(c.iloc[-1]) / base - 1)


def _stack_state(close: float | None, sma20: float | None, sma60: float | None) -> str:
    if close is None or sma20 is None or sma60 is None:
        return "판정 불가 (데이터 부족)"
    if close > sma20 > sma60:
        return "정배열 (종가>20일선>60일선)"
    if close < sma20 < sma60:
        return "역배열 (종가<20일선<60일선)"
    return "혼조"


def _cross_dict(c: lv.CrossState) -> dict:
    return {
        "state": c.state,
        "last_cross": c.last_cross,
        "days_since_cross": c.days_since_cross,
    }


def _first_valid(g: pd.DataFrame, col: str) -> str | None:
    if col not in g.columns or not g[col].notna().any():
        return None
    return str(g[col].dropna().iloc[-1])


def _last(s: pd.Series | None) -> float | None:
    if s is None or not len(s):
        return None
    v = s.dropna()
    return _f(v.iloc[-1]) if len(v) else None


def _mean(values: dict[str, float] | list[float]) -> float | None:
    vals = list(values.values()) if isinstance(values, dict) else list(values)
    return _f(float(np.mean(vals))) if vals else None


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else _f(a - b)


def _cum(returns: pd.Series) -> float | None:
    r = returns.dropna()
    return _f(float((1 + r).prod() - 1)) if len(r) else None


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, 6)
