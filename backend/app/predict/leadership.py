"""섹터 주도권과 순환 -- "지금 무엇이 이끌고, 다음은 어디인가".

이 질문은 이 앱에서 가장 위험한 질문입니다. "다음 주도 섹터"를 묻는 순간
사람들은 근거 없는 답을 기대하고, 모델은 기꺼이 지어냅니다. 그래서 여기서는
**세 개의 서로 다른 층**으로 나눠 각각을 따로 검증합니다.

  1. 지금 누가 이끄는가 (관측)
        초과수익·breadth·참여율을 그대로 보여줍니다. 이건 사실이고 검증이
        필요 없습니다. 다만 "이끈다"는 말이 소수 종목 때문일 수 있으므로
        breadth 와 참여율을 항상 함께 냅니다.

  2. 주도권은 얼마나 이어지는가 (이 데이터에서 측정)
        업종 모멘텀은 문헌 근거가 있는 편입니다 -- Moskowitz & Grinblatt
        (1999, JF) 은 산업 모멘텀이 개별 종목 모멘텀의 상당 부분을 설명한다고
        보고했습니다. 그러나 "문헌에 있으니 여기서도 된다"고 넘기지 않습니다.
        **수집된 유니버스에서 직접 측정**합니다: 20일 상위 업종이 다음 21일에
        실제로 앞섰는가, 순위상관은 얼마인가, t 값은 얼마인가.
        측정값이 0 근처면 그렇다고 화면에 씁니다.

  3. 어떤 업종이 어떤 업종을 선행하는가 (다중검정 보정 후)
        Hong, Torous & Valkanov (2007, JFE) 는 일부 산업이 시장을 최대 2개월
        선행함을 보였고, Menzly & Ozbas (2010, JF) 와 Cohen & Frazzini (2008,
        JF) 는 공급-수요로 엮인 산업이 서로의 수익률을 예측한다고 보고했습니다.
        정보가 산업 네트워크를 따라 천천히 퍼진다는 이야기입니다.

        문제는 **다중검정**입니다. 업종이 30개면 순서쌍이 870개이고, 그중 5% 는
        우연히 유의하게 나옵니다. 그래서 순환 이동(circular rotation) 기반
        순열검정으로 **최대 상관의 귀무분포**를 만들고 그 95백분위를 문턱으로
        씁니다. 문턱을 넘는 쌍이 없으면 "없다"고 답합니다 -- 이 분기가 실제로
        동작해야 이 모듈이 데이터 마이닝 기계가 되지 않습니다.

순환 후보는 (2)와 (3)이 모두 살아 있을 때에만 만들어집니다. 하나라도 죽으면
후보 목록은 비고, 화면은 그 이유를 말합니다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.predict import sectors as sec

# 순열검정 횟수. 늘릴수록 문턱이 안정되지만 화면이 느려집니다. 최대통계량의
# 95백분위만 필요하므로 이 정도면 충분합니다.
N_PERMUTATIONS = 400

# 리드-랙은 주간(5거래일) 비중첩 수익률로 봅니다. 일간은 비동기 거래와
# 마이크로구조 잡음이 섞여 선행성이 아닌 것을 선행성으로 만듭니다.
WEEK = 5
MIN_WEEKS = 60  # 이보다 짧으면 상관계수가 의미를 갖지 못합니다


def sector_wide(panel: pd.DataFrame, *, group_col: str = "industry") -> pd.DataFrame:
    """업종 × 일자 수익률 행렬 (wide). 결측이 많은 업종은 떨어냅니다."""
    agg = sec.aggregate_to_sector(panel, group_col=group_col)
    if agg.empty:
        return pd.DataFrame()
    wide = agg.pivot_table(index="date", columns="sector", values="ret")
    wide = wide.sort_index()
    # 관측이 절반도 없는 업종은 상관을 왜곡합니다.
    keep = wide.columns[wide.notna().mean() >= 0.5]
    return wide[keep]


# ── 1. 지금 누가 이끄는가 ─────────────────────────────────────────────────
def leaders(
    panel: pd.DataFrame, *, group_col: str = "industry", top: int = 5
) -> dict[str, Any]:
    """주도 업종 판정 -- 초과수익만이 아니라 **얼마나 넓게** 오르는지까지.

    수익률만으로 고르면 대형주 한둘이 끌어올린 업종이 '주도 업종'이 됩니다.
    그래서 세 축을 함께 냅니다:

        excess_20d      시장(유니버스 동일가중) 대비 20일 초과수익
        breadth         상승 종목 비율 (최근값)
        participation   구성종목 중 시장을 20일 기준으로 이긴 비율

    `leadership_score` 는 세 축의 z 점수 평균입니다. **검증된 지표가 아닙니다**
    -- 정렬을 위한 편의 수치이며, 화면은 항상 세 축을 함께 보여줍니다.
    """
    wide = sector_wide(panel, group_col=group_col)
    if wide.empty or len(wide) < 25:
        return {"available": False, "reason": "업종 시계열이 짧아 주도권을 판정할 수 없습니다."}

    market = wide.mean(axis=1)
    rows: list[dict] = []
    breadth = sec.sector_breadth(panel, group_col=group_col)
    last_breadth = (
        breadth.sort_values("date").groupby("sector").last()["advancing"].to_dict()
        if len(breadth)
        else {}
    )
    part = _participation(panel, group_col=group_col, window=20)

    for name in wide.columns:
        s = wide[name]
        row = {
            "sector": str(name),
            "ret_5d": _cum(s.tail(5)),
            "ret_20d": _cum(s.tail(20)),
            "ret_60d": _cum(s.tail(60)),
            "excess_5d": _excess(s, market, 5),
            "excess_20d": _excess(s, market, 20),
            "excess_60d": _excess(s, market, 60),
            "breadth": _f(last_breadth.get(name)),
            "participation_20d": _f(part.get(str(name))),
            "n_constituents": int(
                panel[panel[group_col] == name]["ticker"].nunique()
            ),
        }
        rows.append(row)

    axes = ("excess_20d", "breadth", "participation_20d")
    for axis in axes:
        vals = np.array([r[axis] if r[axis] is not None else np.nan for r in rows])
        z = _zscore(vals)
        for r, v in zip(rows, z, strict=True):
            r[f"_z_{axis}"] = None if not np.isfinite(v) else round(float(v), 4)

    for r in rows:
        zs = [r[f"_z_{a}"] for a in axes if r[f"_z_{a}"] is not None]
        r["leadership_score"] = round(float(np.mean(zs)), 4) if zs else None
        for a in axes:
            r.pop(f"_z_{a}")

    ranked = sorted(
        rows,
        key=lambda r: r["leadership_score"] if r["leadership_score"] is not None else -9e9,
        reverse=True,
    )
    return {
        "available": True,
        "as_of": str(pd.Timestamp(wide.index[-1]).date()),
        "n_sectors": len(ranked),
        "leading": ranked[:top],
        "lagging": ranked[-top:][::-1] if len(ranked) > top else [],
        "score_note": (
            "leadership_score 는 초과수익·breadth·참여율의 z 점수 평균입니다. "
            "검증된 지표가 아니라 정렬용 편의 수치이므로, 세 축을 직접 보십시오. "
            "breadth 가 낮은 주도 업종은 소수 종목이 끌어올린 것입니다."
        ),
    }


def _participation(
    panel: pd.DataFrame, *, group_col: str, window: int
) -> dict[str, float]:
    """업종별 '시장을 이긴 종목 비율'. 주도가 넓은지 좁은지를 가릅니다."""
    rets: dict[str, float] = {}
    groups: dict[str, list[str]] = {}
    for t, g in panel.groupby("ticker", sort=False):
        g = g.sort_values("date")
        c = g["close"].dropna().reset_index(drop=True)
        if len(c) <= window:
            continue
        base = float(c.iloc[-1 - window])
        if not np.isfinite(base) or base <= 0:
            continue
        rets[t] = float(c.iloc[-1]) / base - 1
        name = g[group_col].dropna()
        if len(name):
            groups.setdefault(str(name.iloc[-1]), []).append(t)
    if not rets:
        return {}
    market = float(np.mean(list(rets.values())))
    return {
        name: float(np.mean([rets[t] > market for t in members if t in rets]))
        for name, members in groups.items()
        if any(t in rets for t in members)
    }


# ── 2. 주도권은 이어지는가 ────────────────────────────────────────────────
def persistence(
    panel: pd.DataFrame, *, group_col: str = "industry", lookback: int = 20,
    horizon: int = 21,
) -> dict[str, Any]:
    """**이 데이터에서** 업종 모멘텀이 실제로 이어졌는가.

    문헌(Moskowitz & Grinblatt 1999)이 있다고 해서 이 유니버스에서도 성립한다는
    보장은 없습니다. 그래서 직접 잽니다: `horizon` 간격의 겹치지 않는 시점마다
    직전 `lookback` 일 초과수익으로 업종을 정렬하고, 이후 `horizon` 일 초과수익과
    비교합니다.

    돌려주는 것:
        rank_ic       과거 순위 vs 미래 수익 스피어만 상관의 평균
        t_stat        rank_ic 의 t 값 (기간 간 독립 가정 -- 겹치지 않게 잘랐습니다)
        top_minus_bottom  상위 1/3 - 하위 1/3 평균 초과수익 (기간당)
        n_periods     표본 기간 수

    **인샘플이고 비용이 없습니다.** 이 숫자는 '이 유니버스의 과거에 그랬다'는
    서술이지 기대수익이 아닙니다. 업종 수가 적으면 순위상관 자체가 불안정합니다.
    """
    wide = sector_wide(panel, group_col=group_col)
    out: dict[str, Any] = {
        "available": False,
        "reason": "기간이 짧아 지속성을 측정할 수 없습니다.",
        "lookback_days": lookback,
        "horizon_days": horizon,
    }
    if wide.empty or wide.shape[1] < 4 or len(wide) < lookback + horizon * 3:
        return out

    market = wide.mean(axis=1)
    excess = wide.sub(market, axis=0)
    cum = excess.fillna(0.0).cumsum()  # 초과수익의 단순 누적 (로그 근사)

    ics: list[float] = []
    spreads: list[float] = []
    t = lookback
    while t + horizon < len(cum):
        past = cum.iloc[t] - cum.iloc[t - lookback]
        future = cum.iloc[t + horizon] - cum.iloc[t]
        pair = pd.concat([past, future], axis=1, keys=["past", "future"]).dropna()
        if len(pair) >= 4:
            ic = pair["past"].corr(pair["future"], method="spearman")
            if np.isfinite(ic):
                ics.append(float(ic))
            k = max(1, len(pair) // 3)
            ordered = pair.sort_values("past", ascending=False)
            spreads.append(
                float(ordered["future"].head(k).mean() - ordered["future"].tail(k).mean())
            )
        t += horizon

    if len(ics) < 3:
        return out

    arr = np.array(ics)
    se = arr.std(ddof=1) / np.sqrt(len(arr)) if len(arr) > 1 else np.nan
    t_stat = float(arr.mean() / se) if se and np.isfinite(se) and se > 0 else None
    return {
        "available": True,
        "lookback_days": lookback,
        "horizon_days": horizon,
        "n_periods": len(ics),
        "rank_ic": round(float(arr.mean()), 4),
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "top_minus_bottom": round(float(np.mean(spreads)), 4) if spreads else None,
        "verdict": _persistence_verdict(t_stat, len(ics)),
        "caveat": (
            "인샘플 측정이며 거래비용이 없습니다. 업종 수가 적으면 순위상관이 "
            "불안정하고, 기간 수가 적으면 t 값이 과대해집니다. '과거에 그랬다'는 "
            "서술이지 기대수익이 아닙니다."
        ),
    }


def _persistence_verdict(t_stat: float | None, n: int) -> str:
    if t_stat is None:
        return "판정 불가"
    if n < 8:
        return f"표본 기간 {n}개로는 판정할 수 없습니다 (t={t_stat:.2f})"
    if t_stat >= 2.0:
        return "이 데이터에서는 주도권이 이어지는 쪽이었습니다"
    if t_stat <= -2.0:
        return "이 데이터에서는 오히려 **역전**되는 쪽이었습니다"
    return "이 데이터에서는 이어진다는 증거가 없습니다 (|t| < 2)"


# ── 3. 무엇이 무엇을 선행하는가 ───────────────────────────────────────────
def lead_lag(
    panel: pd.DataFrame,
    *,
    group_col: str = "industry",
    lag_weeks: int = 1,
    top: int = 8,
    seed: int = 0,
) -> dict[str, Any]:
    """업종 간 선행-후행 관계 + **다중검정 보정**.

    A 의 이번 주 수익률이 B 의 다음 주 수익률과 상관이 있는지 봅니다. 업종이
    S 개면 순서쌍이 S(S-1) 개이므로, 아무 관계가 없어도 그중 5% 는 p<0.05 로
    나옵니다. 그래서 개별 p 값을 쓰지 않고 **최대통계량의 귀무분포**를 만듭니다.

    귀무분포는 예측 측 행렬을 시간축으로 **순환 이동**시켜 만듭니다. 각 업종의
    자기상관과 업종 간 동시점 상관은 그대로 두고 선행-후행 정렬만 깨는 방법이라,
    단순 셔플보다 보수적이고 현실적입니다.
    """
    wide = sector_wide(panel, group_col=group_col)
    out: dict[str, Any] = {
        "available": False,
        "reason": "주간 관측이 부족해 선행-후행을 볼 수 없습니다.",
        "lag_weeks": lag_weeks,
    }
    if wide.empty or wide.shape[1] < 3:
        return out

    weekly = _weekly(wide)
    if len(weekly) < MIN_WEEKS + lag_weeks:
        return {**out, "n_weeks": len(weekly)}

    names = list(weekly.columns)
    mat = weekly.to_numpy(dtype=float)
    mat = np.where(np.isfinite(mat), mat, 0.0)
    lead = mat[:-lag_weeks]  # 예측 측 (t 주)
    follow = mat[lag_weeks:]  # 피예측 측 (t+lag 주)

    corr = _cross_corr(lead, follow)
    np.fill_diagonal(corr, np.nan)  # 자기 자신은 자기상관이지 선행이 아닙니다

    rng = np.random.default_rng(seed)
    n = lead.shape[0]
    null_max = np.empty(N_PERMUTATIONS)
    for i in range(N_PERMUTATIONS):
        shift = int(rng.integers(5, n - 5)) if n > 12 else 1
        c = _cross_corr(np.roll(lead, shift, axis=0), follow)
        np.fill_diagonal(c, np.nan)
        null_max[i] = np.nanmax(np.abs(c))
    threshold = float(np.nanpercentile(null_max, 95))

    pairs: list[dict] = []
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j or not np.isfinite(corr[i, j]):
                continue
            pairs.append(
                {
                    "leader": str(a),
                    "follower": str(b),
                    "corr": round(float(corr[i, j]), 4),
                    "significant": bool(abs(corr[i, j]) >= threshold),
                }
            )
    pairs.sort(key=lambda p: -abs(p["corr"]))
    n_sig = sum(1 for p in pairs if p["significant"])

    return {
        "available": True,
        "lag_weeks": lag_weeks,
        "n_weeks": len(weekly),
        "n_sectors": len(names),
        "n_pairs_tested": len(pairs),
        "significance_threshold": round(threshold, 4),
        "n_significant": n_sig,
        "pairs": pairs[:top],
        "method": (
            "주간(5거래일) 비중첩 수익률의 교차상관. 문턱은 예측 측을 시간축으로 "
            "순환 이동시킨 400회 순열에서 나온 최대 |상관| 분포의 95백분위입니다 "
            "-- 순서쌍 전체를 한 번에 보정합니다."
        ),
        "verdict": (
            f"보정 후 살아남은 쌍 {n_sig}개"
            if n_sig
            else "다중검정 보정 후 유의한 선행-후행 관계가 없습니다. "
            "업종 간 시차 관계를 주장할 근거가 이 데이터에는 없습니다."
        ),
        "caveat": (
            "상관은 인과가 아닙니다. 유의한 쌍이 나와도 공통 요인(금리·환율·유가)에 "
            "대한 반응 속도 차이일 수 있고, 그 요인은 이 앱의 데이터에 없습니다."
        ),
    }


def _weekly(wide: pd.DataFrame) -> pd.DataFrame:
    """일간 수익률 → 비중첩 주간 수익률. 겹치면 상관이 부풀려집니다."""
    filled = wide.fillna(0.0)
    n_weeks = len(filled) // WEEK
    if n_weeks == 0:
        return filled.iloc[:0]
    trimmed = filled.iloc[len(filled) - n_weeks * WEEK :]
    blocks = np.arange(len(trimmed)) // WEEK
    return (1 + trimmed).groupby(blocks).prod() - 1


def _cross_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """열끼리의 상관행렬 corr[i, j] = corr(a[:, i], b[:, j])."""
    az = a - a.mean(axis=0, keepdims=True)
    bz = b - b.mean(axis=0, keepdims=True)
    an = np.linalg.norm(az, axis=0)
    bn = np.linalg.norm(bz, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (az.T @ bz) / np.outer(an, bn)
    return np.where(np.isfinite(out), out, np.nan)


# ── 순환 후보 ─────────────────────────────────────────────────────────────
def rotation_candidates(
    lead: dict, persist: dict, ll: dict, *, max_n: int = 4
) -> dict[str, Any]:
    """"다음은 어디인가"에 답할 **자격이 있을 때에만** 후보를 냅니다.

    두 조건을 모두 통과해야 합니다:

        1. 선행-후행 관계가 다중검정 보정을 통과했을 것
        2. 그 후행 업종이 아직 덜 움직였을 것 (초과수익이 중앙값 이하)

    지속성(2층)이 죽어 있어도 후보는 냅니다 -- 후보의 근거는 모멘텀 지속이
    아니라 시차 전파이기 때문입니다. 다만 지속성 판정을 함께 실어, 두 근거가
    서로 어긋나면 사용자가 알아채게 합니다.
    """
    if not lead.get("available") or not ll.get("available"):
        return {"available": False, "reason": "주도권 또는 선행-후행 계산이 불가능합니다.",
                "candidates": []}

    sig = [p for p in ll.get("pairs", []) if p["significant"]]
    if not sig:
        return {
            "available": False,
            "reason": ll.get("verdict", "유의한 선행-후행 관계가 없습니다."),
            "candidates": [],
            "what_this_means": (
                "이 데이터로는 '다음 주도 업종'을 말할 근거가 없습니다. 없는 근거를 "
                "지어내는 대신 비워 둡니다."
            ),
        }

    leading_names = {r["sector"] for r in lead["leading"]}
    all_rows = {r["sector"]: r for r in lead["leading"] + lead.get("lagging", [])}
    excesses = [
        r["excess_20d"] for r in all_rows.values() if r.get("excess_20d") is not None
    ]
    median = float(np.median(excesses)) if excesses else 0.0

    seen: set[str] = set()
    out: list[dict] = []
    for p in sig:
        if p["leader"] not in leading_names or p["follower"] in seen:
            continue
        row = all_rows.get(p["follower"])
        moved = row["excess_20d"] if row and row.get("excess_20d") is not None else None
        if moved is not None and moved > median:
            continue  # 이미 움직였다면 '다음'이 아닙니다
        seen.add(p["follower"])
        out.append(
            {
                "sector": p["follower"],
                "led_by": p["leader"],
                "corr": p["corr"],
                "direction": "같은 방향" if p["corr"] > 0 else "반대 방향",
                "excess_20d": moved,
                "condition": (
                    f"{p['leader']} 의 강세가 이어지고, 과거의 시차 관계가 "
                    f"유지된다면 (주간 상관 {p['corr']:+.2f}, "
                    f"{ll['lag_weeks']}주 시차)"
                ),
            }
        )
        if len(out) >= max_n:
            break

    return {
        "available": bool(out),
        "reason": None if out else "유의한 쌍의 후행 업종이 이미 모두 움직였습니다.",
        "candidates": out,
        "persistence_verdict": persist.get("verdict"),
        "how_to_read": (
            "이것은 '오를 업종'이 아니라 '과거에 시차 관계가 있었고 아직 덜 움직인 "
            "업종'입니다. 조건문이 성립하지 않으면 근거도 사라집니다."
        ),
    }


# ── 유틸 ─────────────────────────────────────────────────────────────────
def _cum(s: pd.Series) -> float | None:
    r = s.dropna()
    return _f(float((1 + r).prod() - 1)) if len(r) else None


def _excess(s: pd.Series, market: pd.Series, window: int) -> float | None:
    a, b = _cum(s.tail(window)), _cum(market.tail(window))
    return None if a is None or b is None else _f(a - b)


def _zscore(v: np.ndarray) -> np.ndarray:
    ok = np.isfinite(v)
    if ok.sum() < 2:
        return np.full_like(v, np.nan, dtype=float)
    mu, sd = v[ok].mean(), v[ok].std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return np.where(ok, 0.0, np.nan)
    return (v - mu) / sd


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, 6)
