"""가격대(zone) 엔진 -- 지지·저항을 '한 개의 선'이 아니라 '근거가 겹치는 구간'으로.

기존 `levels.swing_levels` 는 스윙 피벗 하나만 봅니다. 그 방법의 학술적 근거는
약하고(모듈 상단 주석 참고), 무엇보다 **분할 매수·매도에 쓸 수 없습니다** --
정확히 71,200원이라는 선을 주면 사용자는 그 가격에 지정가를 걸고, 71,250원에서
반등하면 놓칩니다. 실제로 지지·저항은 폭을 가진 구간입니다.

그래서 이 모듈은 셋을 바꿉니다.

  1. **여러 방법을 독립적으로 돌리고 겹치는 곳을 찾습니다(confluence).**
     한 방법만 가리키는 가격보다 서로 다른 근거가 겹치는 가격이 더 자주
     의식됩니다. 각 후보에는 어떤 방법이 왜 그 가격을 지목했는지가 남습니다.
  2. **결과는 구간(low~high)입니다.** 폭은 ATR 로 정합니다. 변동성이 큰 종목은
     넓은 구간, 조용한 종목은 좁은 구간이 됩니다. 분할 주문은 이 구간에
     걸립니다.
  3. **도달 확률을 함께 계산합니다.** "여기가 지지선이다"만으로는 분할 비중을
     정할 수 없습니다. 21거래일 안에 그 가격을 건드릴 확률이 있어야 비중을
     확률에 비례시킬 수 있습니다.

방법별 근거 등급 (`METHODS` 의 weight):

    거래량 밀집대 (1.0)
        Grinblatt & Han (2005, JFE) -- 처분효과로 생긴 미실현손익 오버행이
        수익률을 횡단면에서 예측합니다. 많은 물량이 체결된 가격은 보유자
        다수의 취득단가이고, 그 부근에서 매도 압력·본전 심리가 실제로 생깁니다.
        Frazzini (2006, JF) 도 같은 방향입니다. 지지·저항류 근거 중 가장 강합니다.

    52주 고가/저가 (1.0)
        George & Hwang (2004, JF) -- 52주 신고가 근접도가 모멘텀보다 강한
        예측 변수였습니다. Huddart, Lang & Yetman (2009, RFS) 는 52주 고·저가
        부근에서 거래량이 실제로 튀는 것을 보였습니다. Li & Yu (2012, JFE) 는
        이를 심리적 앵커로 해석합니다.

    스윙 피벗 클러스터 (0.7)
        Osler (2000, FRBNY Economic Policy Review) -- 공표된 지지·저항 수준
        부근에서 추세 중단이 유의하게 잦았습니다. Osler (2003, JF) 는 이유를
        주문 군집으로 설명합니다(이익실현 주문은 라운드넘버에 몰려 반등을,
        손절 주문은 그 너머에 몰려 급격한 이탈을 만듭니다). 유의하지만 효과
        크기는 작고 주로 외환 인트라데이 증거입니다.

    라운드 넘버 (0.6)
        Osler (2003, JF); Sonnemans (2006, JEBO) 가격 군집; Bhattacharya,
        Holden & Jacobsen (2012, Management Science) 라운드넘버 전후의 매수·
        매도 불균형. 존재는 반복 확인됐지만 크기가 작습니다.

    주요 이동평균 (0.4)
        Brock, Lakonishok & LeBaron (1992, JF) 은 이동평균 규칙의 초과수익을
        보고했으나, Sullivan, Timmermann & White (1999, JF) 가 데이터 스누핑을
        보정하자 대부분 사라졌습니다. **가장 약한 근거**라서 가중치가 낮습니다.
        여기서는 "많은 사람이 보는 선"이라는 이유로만 후보에 넣습니다.

    최근 N일 극값 (0.4)
        단기 되돌림의 참조점. 별도 문헌 근거로 두지 않습니다.

이 모듈이 하지 않는 것: 매매 판단. 확률과 구간과 근거만 돌려줍니다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.indicators import price as px

# (표시명, 근거 가중치, 근거 문구). 화면과 프롬프트가 같은 문구를 씁니다.
METHODS: dict[str, tuple[str, float, str]] = {
    "volume_node": (
        "거래량 밀집대",
        1.0,
        "많은 물량이 체결된 가격대. 보유자 다수의 취득단가라 매도 압력·본전 "
        "심리가 실제로 생깁니다 (Grinblatt & Han 2005; Frazzini 2006).",
    ),
    "extreme_52w": (
        "52주 고가/저가",
        1.0,
        "널리 인용되는 앵커. 이 부근에서 거래량이 튀고 수익률 예측력이 보고된 "
        "바 있습니다 (George & Hwang 2004; Huddart et al. 2009).",
    ),
    "swing": (
        "스윙 피벗 클러스터",
        0.7,
        "과거에 반복해서 되돌려진 가격대. 유의하지만 효과 크기는 작습니다 "
        "(Osler 2000, 2003).",
    ),
    "round": (
        "라운드 넘버",
        0.6,
        "주문이 몰리는 심리적 가격. 이익실현 주문은 여기에, 손절 주문은 그 "
        "너머에 쌓입니다 (Osler 2003; Bhattacharya et al. 2012).",
    ),
    "moving_average": (
        "주요 이동평균",
        0.4,
        "많은 참여자가 같은 선을 봅니다. 다만 데이터 스누핑 보정 후 초과수익은 "
        "대부분 사라졌습니다 (Sullivan et al. 1999). 근거가 가장 약합니다.",
    ),
    "recent_extreme": (
        "최근 20일 극값",
        0.4,
        "단기 되돌림의 참조점. 별도 문헌 근거는 두지 않습니다.",
    ),
}


@dataclass(frozen=True)
class Candidate:
    method: str
    price: float
    quality: float  # 0~1. 같은 방법 안에서의 강도 (터치 수, 거래대금 비중 등)
    detail: str


@dataclass(frozen=True)
class Zone:
    kind: str  # support | resistance
    price: float  # 대표가 (근거 가중평균)
    low: float
    high: float
    distance_pct: float  # 현재가 대비 (음수 = 아래)
    distance_atr: float  # 현재가 대비 거리를 ATR 배수로. 변동성 대비 멀고 가까움
    score: float
    confidence: str  # 높음 | 보통 | 낮음
    n_methods: int
    methods: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    touch_prob_21d: float | None = None
    sigma_days: float | None = None  # 이 거리가 1σ 가 되는 거래일 수

    def to_dict(self) -> dict:
        return asdict(self)


# ── 도달 확률 ─────────────────────────────────────────────────────────────
def touch_probability(
    spot: float, barrier: float, vol_annual: float, horizon_days: int
) -> float | None:
    """무추세 기하브라운운동에서 `horizon_days` 안에 `barrier` 를 **한 번이라도**
    건드릴 확률.

    반사원리(reflection principle)로부터

        P = 2 · Φ( -|ln(barrier/spot)| / (σ√T) )

    입니다. 종가 확률이 아니라 **터치 확률**이라는 점이 중요합니다 -- 분할
    주문은 장중에 체결되므로 종가 기준 확률을 쓰면 체결 가능성을 과소평가합니다.

    이 값을 점 예측으로 읽으면 안 됩니다. 가정이 셋이나 들어갑니다:

        * 추세 없음 -- 상승·하락 추세가 있으면 한쪽 확률이 실제보다 낮게 나옵니다.
        * 변동성 일정 -- 실제 변동성은 군집합니다 (Bollerslev 1986). 조용한
          구간에서 계산하면 과소, 급등락 직후면 과대 추정됩니다.
        * 정규분포 -- 실제 수익률은 꼬리가 두꺼워 **먼 가격의 도달 확률은
          이 계산보다 높습니다**.

    그래서 이 숫자는 "어디에 더 많은 비중을 둘까"의 상대적 저울로만 씁니다.
    """
    if not all(np.isfinite([spot, barrier, vol_annual])):
        return None
    if spot <= 0 or barrier <= 0 or vol_annual <= 0 or horizon_days <= 0:
        return None
    sigma_t = vol_annual * math.sqrt(horizon_days / 252.0)
    if sigma_t <= 0:
        return None
    z = abs(math.log(barrier / spot)) / sigma_t
    p = 2.0 * _norm_cdf(-z)
    return round(min(1.0, max(0.0, p)), 4)


def sigma_days(spot: float, barrier: float, vol_annual: float) -> float | None:
    """그 거리가 1σ 움직임이 되는 거래일 수. '얼마나 먼 가격인가'의 시간 환산."""
    if not all(np.isfinite([spot, barrier, vol_annual])):
        return None
    if spot <= 0 or barrier <= 0 or vol_annual <= 0:
        return None
    d = abs(math.log(barrier / spot))
    return round(252.0 * (d / vol_annual) ** 2, 1)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── 후보 생성 ─────────────────────────────────────────────────────────────
def _swing_candidates(
    high: pd.Series, low: pd.Series, tol: float, half_life: float = 250.0
) -> list[Candidate]:
    """좌우 5봉보다 높은/낮은 피벗을 모아 tol 이내로 묶습니다.

    `levels.swing_levels` 와 달리 **마지막 터치가 언제였는지**를 셉니다.
    3년 전에 세 번 되돌려진 가격과 지난달에 세 번 되돌려진 가격은 같은
    무게일 수 없습니다.
    """
    w = 5
    m = len(high)
    if m < w * 2 + 1:
        return []

    pivots: list[tuple[int, float]] = []
    h = high.reset_index(drop=True)
    lo = low.reset_index(drop=True)
    for i in range(w, m - w):
        if np.isfinite(h.iloc[i]) and h.iloc[i] == h.iloc[i - w : i + w + 1].max():
            pivots.append((i, float(h.iloc[i])))
        if np.isfinite(lo.iloc[i]) and lo.iloc[i] == lo.iloc[i - w : i + w + 1].min():
            pivots.append((i, float(lo.iloc[i])))
    if not pivots:
        return []

    pivots.sort(key=lambda x: x[1])
    clusters: list[list[tuple[int, float]]] = [[pivots[0]]]
    for idx, p in pivots[1:]:
        if p <= clusters[-1][-1][1] * (1 + tol):
            clusters[-1].append((idx, p))
        else:
            clusters.append([(idx, p)])

    out: list[Candidate] = []
    for c in clusters:
        touches = len(c)
        price = float(np.mean([p for _, p in c]))
        age = m - 1 - max(i for i, _ in c)
        # 오래된 수준은 감쇠시키되 0 으로는 만들지 않습니다. 몇 년 전 고점도
        # 여전히 회자되는 가격일 수 있습니다.
        recency = max(0.35, math.exp(-age / half_life))
        quality = min(1.0, (0.35 + 0.22 * touches)) * recency
        out.append(
            Candidate(
                "swing",
                price,
                round(quality, 4),
                f"피벗 {touches}회 (마지막 터치 {age}거래일 전)",
            )
        )
    return out


def _volume_node_candidates(
    df: pd.DataFrame, *, bins: int = 40, top_k: int = 5
) -> list[Candidate]:
    """거래대금 밀집대(HVN).

    하루 거래대금을 그날의 저가~고가 구간에 **균등 분배**해 가격 축 히스토그램을
    만듭니다. 종가 한 점에 몰아주면 장중에 넓게 오간 날의 정보가 사라집니다.
    이웃 구간보다 높은 국소 최대만 남겨 '봉우리'를 고릅니다.
    """
    if df.empty:
        return []
    lo_all = float(np.nanmin(df["low"]))
    hi_all = float(np.nanmax(df["high"]))
    if not np.isfinite(lo_all) or not np.isfinite(hi_all) or hi_all <= lo_all:
        return []

    weight = df["value"] if "value" in df.columns else None
    if weight is None or weight.notna().sum() < len(df) * 0.5:
        # 거래대금이 없으면 거래량 × 종가로 대체합니다. 그것도 없으면 포기합니다.
        if "volume" not in df.columns or df["volume"].notna().sum() < len(df) * 0.5:
            return []
        weight = df["volume"] * df["close"]

    edges = np.linspace(lo_all, hi_all, bins + 1)
    hist = np.zeros(bins)
    for (_, row), wv in zip(df.iterrows(), weight, strict=False):
        if not np.isfinite(wv) or wv <= 0:
            continue
        d_lo, d_hi = float(row["low"]), float(row["high"])
        if not np.isfinite(d_lo) or not np.isfinite(d_hi):
            continue
        i0 = int(np.searchsorted(edges, d_lo, "right") - 1)
        i1 = int(np.searchsorted(edges, d_hi, "right") - 1)
        i0, i1 = max(0, min(bins - 1, i0)), max(0, min(bins - 1, i1))
        span = i1 - i0 + 1
        hist[i0 : i1 + 1] += wv / span

    total = hist.sum()
    if total <= 0:
        return []
    share = hist / total

    peaks: list[tuple[int, float]] = []
    for i in range(bins):
        left = share[i - 1] if i > 0 else -1.0
        right = share[i + 1] if i < bins - 1 else -1.0
        if share[i] >= left and share[i] >= right and share[i] > 1.3 / bins:
            peaks.append((i, float(share[i])))
    peaks.sort(key=lambda x: -x[1])

    out: list[Candidate] = []
    for i, s in peaks[:top_k]:
        center = float((edges[i] + edges[i + 1]) / 2)
        out.append(
            Candidate(
                "volume_node",
                center,
                round(min(1.0, s / 0.06), 4),  # 6% 비중이면 만점
                f"이 구간에서 기간 거래대금의 {s * 100:.1f}% 체결",
            )
        )
    return out


def _round_candidates(spot: float, *, max_dist: float = 0.22) -> list[Candidate]:
    """심리적 라운드 넘버. 굵은 격자일수록 더 많이 의식됩니다."""
    if not np.isfinite(spot) or spot <= 0:
        return []
    mag = 10 ** math.floor(math.log10(spot))
    out: list[Candidate] = []
    seen: set[float] = set()
    for step, quality, label in ((mag / 2, 1.0, "굵은"), (mag / 10, 0.55, "가는")):
        if step <= 0:
            continue
        for k in (math.floor(spot / step), math.ceil(spot / step)):
            level = round(k * step, 6)
            if level <= 0 or level in seen:
                continue
            if abs(level / spot - 1) > max_dist:
                continue
            seen.add(level)
            out.append(
                Candidate("round", float(level), quality, f"{label} 라운드 넘버 격자")
            )
    return out


def _ma_candidates(close: pd.Series, spot: float) -> list[Candidate]:
    """주요 이동평균선. 긴 선일수록 보는 사람이 많다는 이유로만 가중합니다."""
    out: list[Candidate] = []
    for window, quality in ((200, 1.0), (120, 0.85), (60, 0.7), (20, 0.55)):
        s = px.sma(close, window).dropna()
        if not len(s):
            continue
        v = float(s.iloc[-1])
        if not np.isfinite(v) or v <= 0 or abs(v / spot - 1) > 0.25:
            continue
        out.append(Candidate("moving_average", v, quality, f"{window}일 이동평균"))
    return out


def _extreme_candidates(df: pd.DataFrame, spot: float) -> list[Candidate]:
    out: list[Candidate] = []
    close = df["close"]
    if len(close) >= 120:
        window = min(252, len(close))
        hi = float(np.nanmax(df["high"].tail(window)))
        lo = float(np.nanmin(df["low"].tail(window)))
        label = "52주" if window >= 252 else f"{window}거래일"
        for v, what in ((hi, "고가"), (lo, "저가")):
            if np.isfinite(v) and v > 0:
                out.append(Candidate("extreme_52w", v, 1.0, f"{label} {what}"))
    if len(close) >= 20:
        hi20 = float(np.nanmax(df["high"].tail(20)))
        lo20 = float(np.nanmin(df["low"].tail(20)))
        for v, what in ((hi20, "고가"), (lo20, "저가")):
            if np.isfinite(v) and v > 0 and abs(v / spot - 1) > 0.005:
                out.append(Candidate("recent_extreme", v, 0.7, f"최근 20일 {what}"))
    return out


# ── 병합 · 선택 ───────────────────────────────────────────────────────────
def price_zones(
    df: pd.DataFrame,
    *,
    lookback: int = 250,
    max_per_side: int = 3,
    horizon_days: int = 21,
) -> dict:
    """지지 3개 · 저항 3개(구간)와 그 근거를 계산합니다.

    반환값의 `supports` 는 현재가에 **가까운 순**, `resistances` 도 가까운
    순입니다. 분할 주문은 가까운 것부터 채워지므로 그 순서가 곧 1차·2차·3차입니다.
    """
    empty = {
        "as_of": None,
        "spot": None,
        "atr_14": None,
        "vol_annual": None,
        "horizon_days": horizon_days,
        "supports": [],
        "resistances": [],
        "method_notes": _method_notes(),
        "insufficient": True,
        "note": "가격대 계산에는 최소 60거래일이 필요합니다.",
    }
    if df is None or df.empty or len(df) < 60:
        return empty

    d = df.sort_values("date").tail(lookback).reset_index(drop=True)
    close = d["close"]
    spot = float(close.iloc[-1])
    if not np.isfinite(spot) or spot <= 0:
        return empty

    atr_series = px.atr(d["high"], d["low"], close, 14).dropna()
    atr = float(atr_series.iloc[-1]) if len(atr_series) else float("nan")
    if not np.isfinite(atr) or atr <= 0:
        atr = spot * 0.02  # ATR 을 못 구하면 2% 를 대용폭으로

    vol_series = px.realized_volatility(close, 20).dropna()
    vol_annual = float(vol_series.iloc[-1]) if len(vol_series) else float("nan")
    if not np.isfinite(vol_annual) or vol_annual <= 0:
        vol_annual = float("nan")

    # 병합 허용폭: 변동성이 큰 종목은 넓게 묶어야 같은 가격대가 쪼개지지 않습니다.
    tol = max(0.010, min(0.045, 0.7 * atr / spot))

    candidates: list[Candidate] = []
    candidates += _swing_candidates(d["high"], d["low"], tol)
    candidates += _volume_node_candidates(d)
    candidates += _round_candidates(spot)
    candidates += _ma_candidates(close, spot)
    candidates += _extreme_candidates(d, spot)
    candidates = [c for c in candidates if np.isfinite(c.price) and c.price > 0]
    if not candidates:
        return {**empty, "spot": round(spot, 4), "note": "후보 가격대를 찾지 못했습니다."}

    zones = _merge(candidates, spot=spot, atr=atr, tol=tol)
    zones = [_with_probability(z, spot, vol_annual, horizon_days) for z in zones]

    supports = sorted(
        [z for z in zones if z.kind == "support"], key=lambda z: -z.price
    )
    resistances = sorted(
        [z for z in zones if z.kind == "resistance"], key=lambda z: z.price
    )

    return {
        "as_of": str(pd.Timestamp(d["date"].iloc[-1]).date()),
        "spot": round(spot, 4),
        "atr_14": round(atr, 4),
        "atr_pct": round(atr / spot, 4),
        "vol_annual": round(vol_annual, 4) if np.isfinite(vol_annual) else None,
        "horizon_days": horizon_days,
        "supports": [z.to_dict() for z in _pick(supports, spot, atr, max_per_side)],
        "resistances": [
            z.to_dict() for z in _pick(resistances, spot, atr, max_per_side)
        ],
        "method_notes": _method_notes(),
        "insufficient": False,
        "note": (
            "지지·저항은 점이 아니라 구간입니다. 구간 폭은 ATR(14) 로 정했고, "
            "여러 방법이 겹칠수록 점수가 높습니다. 확률은 무추세·정규분포 가정의 "
            "터치 확률이며 예측이 아닙니다."
        ),
    }


def _merge(
    candidates: list[Candidate], *, spot: float, atr: float, tol: float
) -> list[Zone]:
    """가격이 가까운 후보들을 하나의 구간으로. **서로 다른 방법이 겹치면 가산.**

    이웃 간 거리(tol)만으로 묶으면 후보가 촘촘한 구간에서 사슬처럼 이어져
    폭이 5% 를 넘는 '구간'이 나옵니다. 그건 구간이 아니라 그냥 넓은 영역이고,
    거기에 지정가를 걸 수는 없습니다. 그래서 **그룹 전체의 폭**도 함께 제한합니다.
    """
    max_span = min(0.035, max(0.015, 1.6 * atr / spot))
    ordered = sorted(candidates, key=lambda c: c.price)
    groups: list[list[Candidate]] = [[ordered[0]]]
    for c in ordered[1:]:
        near = c.price <= groups[-1][-1].price * (1 + tol)
        within_span = c.price <= groups[-1][0].price * (1 + max_span)
        if near and within_span:
            groups[-1].append(c)
        else:
            groups.append([c])

    zones: list[Zone] = []
    for g in groups:
        weights = [METHODS[c.method][1] * c.quality for c in g]
        total_w = sum(weights)
        if total_w <= 0:
            continue
        price = float(np.average([c.price for c in g], weights=weights))
        methods = sorted({c.method for c in g})

        # 컨플루언스 가산: 방법이 늘수록 점수가 오르되 선형보다 완만하게.
        # 같은 방법을 여러 번 세어 점수가 부풀지 않도록 방법별 최댓값만 씁니다.
        best_per_method: dict[str, float] = {}
        for c, w in zip(g, weights, strict=True):
            best_per_method[c.method] = max(best_per_method.get(c.method, 0.0), w)
        base = sum(best_per_method.values())
        score = base * (1.0 + 0.18 * (len(methods) - 1))

        lo = min(c.price for c in g)
        hi = max(c.price for c in g)
        pad = 0.25 * atr
        lo, hi = lo - pad, hi + pad
        kind = "support" if price <= spot else "resistance"
        # 구간이 현재가를 넘어가면 방향이 뒤섞입니다. 현재가에서 잘라냅니다.
        if kind == "support":
            hi = min(hi, spot)
            lo = min(lo, hi - 1e-9)
        else:
            lo = max(lo, spot)
            hi = max(hi, lo + 1e-9)

        zones.append(
            Zone(
                kind=kind,
                price=round(price, 4),
                low=round(lo, 4),
                high=round(hi, 4),
                distance_pct=round(price / spot - 1, 4),
                distance_atr=round((price - spot) / atr, 2) if atr else 0.0,
                score=round(score, 3),
                confidence=_confidence(score, len(methods)),
                n_methods=len(methods),
                methods=[METHODS[m][0] for m in methods],
                evidence=[
                    {
                        "method": METHODS[c.method][0],
                        "price": round(c.price, 4),
                        "quality": c.quality,
                        "detail": c.detail,
                    }
                    for c in sorted(
                        g, key=lambda c: -METHODS[c.method][1] * c.quality
                    )
                ],
            )
        )
    return zones


def _with_probability(
    z: Zone, spot: float, vol_annual: float, horizon_days: int
) -> Zone:
    """확률은 **구간 경계가 아니라 대표가** 기준으로 계산합니다.

    처음에는 '먼저 닿는 쪽 경계'를 썼는데, 경계는 ATR 만큼 넓혀 현재가 쪽으로
    붙어 있어서 어떤 구간이든 도달 확률이 0.95 를 넘었습니다. 그러면 비중이
    전부 1차에 쏠려 분할이 아니게 됩니다. 대표가는 근거가 실제로 몰린 가격이라
    "그 가격대에 도달했다"는 판정에 더 맞습니다.
    """
    return Zone(
        **{
            **z.to_dict(),
            "touch_prob_21d": touch_probability(
                spot, z.price, vol_annual, horizon_days
            ),
            "sigma_days": sigma_days(spot, z.price, vol_annual),
        }
    )


def _pick(zones: list[Zone], spot: float, atr: float, k: int) -> list[Zone]:
    """가까운 순으로 고르되 **너무 붙은 구간은 건너뜁니다**.

    두 가지를 거릅니다:

      * 현재가에서 0.5 ATR 도 떨어지지 않은 구간. 그건 지지·저항이 아니라 그냥
        지금 가격이고, 거기에 지정가를 걸면 분할이 아니라 즉시 매수입니다.
      * 서로 0.6 ATR 이내로 붙은 구간. 0.3 ATR 간격의 세 지지선을 주면 한 번의
        하락에 세 주문이 동시에 체결되어 결국 한 번에 산 것과 같아집니다.
    """
    chosen: list[Zone] = []
    min_gap = 0.6 * atr
    min_from_spot = max(0.5 * atr, 0.012 * spot)
    zones = [z for z in zones if abs(z.price - spot) >= min_from_spot]
    for z in zones:
        if any(abs(z.price - c.price) < min_gap for c in chosen):
            # 점수가 더 높으면 자리를 빼앗습니다.
            worse = [c for c in chosen if abs(z.price - c.price) < min_gap]
            if all(z.score <= c.score for c in worse):
                continue
            chosen = [c for c in chosen if c not in worse]
        chosen.append(z)
        if len(chosen) >= k:
            break
    return chosen[:k]


def _confidence(score: float, n_methods: int) -> str:
    if score >= 1.8 and n_methods >= 3:
        return "높음"
    if score >= 1.0 and n_methods >= 2:
        return "보통"
    return "낮음"


def _method_notes() -> list[dict]:
    return [
        {"method": label, "evidence_weight": w, "basis": basis}
        for label, w, basis in METHODS.values()
    ]
