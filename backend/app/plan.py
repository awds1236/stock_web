"""분할 매수·매도 골격 -- **추천이 아니라 산술**.

이 모듈은 "사라/팔라"를 말하지 않습니다. 사용자가 이미 그 종목을 매매하기로
정했다는 전제 아래, **어디에 얼마씩 나눌 것인가**의 계산만 합니다. 판단은
사용자 몫이고, 이 모듈은 그 판단을 숫자로 옮기는 자리입니다.

왜 이런 골격이 필요한가 (그리고 무엇을 기대하면 안 되는가):

  * **분할 매수는 기대수익을 높이는 기법이 아닙니다.** Constantinides (1979,
    JFQA) 는 기대효용 틀에서 정액분할매수가 열등하다는 것을 보였고,
    Shtekhman 외 (Vanguard, 2012) 의 시뮬레이션에서도 일시금 투자가 약 2/3
    구간에서 분할을 이겼습니다. 자산은 평균적으로 오르므로 현금으로 기다리는
    시간은 비용입니다. 분할이 줄이는 것은 **진입 시점 위험(과 후회)** 이지
    기대수익이 아닙니다. 이 사실을 숨기면 이 화면은 광고가 됩니다.

  * **분할 매도에는 다른 근거가 있습니다.** Odean (1998, JF) 의 처분효과 --
    투자자는 오른 종목을 너무 빨리 팔고 내린 종목을 너무 오래 들고 있습니다.
    미리 정해둔 청산 사다리는 그 편향에 대한 사전 약속(pre-commitment)으로
    작동합니다. 매수보다 매도 쪽에서 분할의 정당성이 더 큽니다.

  * **회전율은 비용입니다.** Barber & Odean (2000, JF) -- 거래가 잦은 계좌일수록
    수익률이 낮았습니다. 단계를 늘릴수록 수수료·세금·슬리피지가 늘어납니다.
    그래서 이 앱은 단계를 3개로 제한합니다.

  * **비중은 도달 확률에 비례시킵니다.** 아래에 세 개의 지정가를 걸어두면
    가격이 안 내려올 때 아무것도 못 삽니다. 그 확률은 계산할 수 있고
    (`zones.touch_probability`), 그만큼을 '즉시 체결분'으로 돌리는 것이
    일시금이 평균적으로 유리하다는 위 결과와도 일치합니다.

  * **손실 한계선(무효화 가격)이 먼저입니다.** 변동성 기준 사이징의 근거는
    Moreira & Muir (2017, JF), Harvey 외 (2018) 입니다 -- 변동성에 맞춰 노출을
    조절하면 위험조정성과가 개선됩니다. 여기서는 ATR 로 무효화 가격을 잡고,
    "계좌의 1% 를 잃을 각오라면 최대 투입 비중은 얼마인가"를 역산합니다.
"""

from __future__ import annotations

from typing import Any

import numpy as np

MAX_STEPS = 3

CAVEATS = [
    "이것은 매매 추천이 아니라, 매매하기로 이미 정한 경우의 체결 구조 계산입니다.",
    "분할 매수는 기대수익을 높이지 않습니다. Constantinides(1979)·Vanguard(2012) "
    "기준으로 일시금 투자가 평균적으로 우월하며, 분할이 줄이는 것은 진입 시점 "
    "위험과 후회입니다.",
    "비중의 근거인 도달 확률은 무추세·정규분포·일정 변동성 가정에서 나온 값입니다. "
    "실제 수익률은 꼬리가 두꺼워 먼 가격의 도달 확률은 계산보다 높습니다.",
    "단계마다 수수료·세금·슬리피지가 붙습니다 (Barber & Odean 2000).",
    "지지·저항이 뚫리면 그 가격은 지지가 아니라 저항이 됩니다. 사다리는 하락이 "
    "'되돌림'일 때만 유효하고, 추세 전환일 때는 물타기가 됩니다.",
]


def scaled_plan(
    *,
    spot: float,
    supports: list[dict],
    resistances: list[dict],
    atr: float | None,
    horizon_days: int = 21,
) -> dict[str, Any]:
    """지지 구간 → 분할 매수, 저항 구간 → 분할 매도 골격.

    반환하는 비중은 전부 **한 종목 안에서의 상대 비중**입니다 (합 1.0).
    계좌 대비 얼마를 넣을지는 `risk` 블록이 따로 계산합니다.
    """
    if not np.isfinite(spot) or spot <= 0:
        return {"available": False, "reason": "현재가를 확인할 수 없습니다."}
    if not supports and not resistances:
        return {"available": False, "reason": "가격대를 계산할 데이터가 부족합니다."}

    atr_v = float(atr) if atr and np.isfinite(atr) and atr > 0 else spot * 0.02

    entry = _entry_ladder(spot, supports[:MAX_STEPS], horizon_days)
    invalidation = _invalidation(spot, supports[:MAX_STEPS], atr_v)
    exit_ = _exit_ladder(spot, resistances[:MAX_STEPS], horizon_days)
    risk = _risk(entry.get("avg_cost_if_all_filled"), invalidation, exit_)

    return {
        "available": True,
        "horizon_days": horizon_days,
        "spot": round(spot, 4),
        "atr_14": round(atr_v, 4),
        "entry": entry,
        "invalidation": invalidation,
        "exit": exit_,
        "risk": risk,
        "caveats": CAVEATS,
    }


# ── 매수 사다리 ───────────────────────────────────────────────────────────
def _entry_ladder(spot: float, supports: list[dict], horizon: int) -> dict[str, Any]:
    """비중 = 도달 확률 비례 + '안 내려올 확률' 만큼의 즉시 체결분.

    확률이 없는 종목(변동성 계산 불가)은 균등 분할로 물러섭니다 -- 근거 없는
    확률을 지어내느니 균등이 정직합니다.
    """
    steps: list[dict] = []
    probs = [s.get("touch_prob_21d") for s in supports]
    usable = [p for p in probs if isinstance(p, int | float)]

    if supports and len(usable) == len(supports) and sum(usable) > 0:
        p_first = float(usable[0])
        immediate = round(max(0.0, 1.0 - p_first), 4)
        pool = 1.0 - immediate
        total = sum(usable)
        weights = [pool * (p / total) for p in usable]
        basis = (
            "1차 지지까지 내려오지 않을 확률만큼은 즉시 체결분으로 두고, 나머지를 "
            "각 구간의 도달 확률에 비례해 나눴습니다."
        )
    else:
        immediate = round(1.0 / (len(supports) + 1), 4) if supports else 1.0
        pool = 1.0 - immediate
        weights = [pool / len(supports)] * len(supports) if supports else []
        basis = "도달 확률을 계산할 수 없어 균등 분할로 대체했습니다."

    # 반올림 잔차를 그대로 두면 투입 비중 합이 1.0001 처럼 나옵니다. 화면에
    # 100.01% 라고 찍히면 계산이 틀린 것으로 보이므로 여기서 정확히 맞춥니다.
    immediate, weights = _exact(immediate, weights)

    if immediate > 0:
        steps.append(
            {
                "label": "즉시",
                "kind": "market",
                "price": round(spot, 4),
                "low": round(spot, 4),
                "high": round(spot, 4),
                "weight": immediate,
                "distance_pct": 0.0,
                "touch_prob_21d": 1.0,
                "confidence": None,
                "why": "지정가만 걸면 가격이 안 내려올 때 한 주도 못 삽니다.",
            }
        )
    for i, (s, w) in enumerate(zip(supports, weights, strict=True), start=1):
        steps.append(
            {
                "label": f"{i}차 지지",
                "kind": "limit",
                "price": s["price"],
                "low": s["low"],
                "high": s["high"],
                "weight": round(w, 4),
                "distance_pct": s["distance_pct"],
                "touch_prob_21d": s.get("touch_prob_21d"),
                "confidence": s.get("confidence"),
                "why": " · ".join(s.get("methods", [])) or "근거 미상",
            }
        )

    filled = _fill_paths(steps)
    return {
        "steps": steps,
        "weighting": "touch_probability" if len(usable) == len(supports) and supports else "equal",
        "weighting_basis": basis,
        "avg_cost_if_all_filled": filled[-1]["avg_cost"] if filled else None,
        "partial_fills": filled,
        "prob_no_limit_fill_21d": (
            round(1.0 - float(usable[0]), 4) if usable else None
        ),
    }


def _exact(immediate: float, weights: list[float]) -> tuple[float, list[float]]:
    """비중 합을 정확히 1.0 으로. 잔차는 가장 큰 비중에 몰아넣습니다."""
    vals = [round(immediate, 4)] + [round(w, 4) for w in weights]
    residual = round(1.0 - sum(vals), 4)
    if residual and vals:
        i = max(range(len(vals)), key=lambda k: vals[k])
        vals[i] = round(vals[i] + residual, 4)
    return vals[0], vals[1:]


def _fill_paths(steps: list[dict]) -> list[dict]:
    """1단계만 체결 / 2단계까지 / 전부 -- 각 경우의 평균단가와 투입 비중.

    전부 체결됐을 때의 평균단가만 보여주면 착시가 생깁니다. 실제로는 앞 단계만
    체결된 채 반등하는 경우가 가장 흔하고, 그때 평균단가는 훨씬 높습니다.
    """
    out: list[dict] = []
    used = 0.0
    cost = 0.0
    for i, s in enumerate(steps, start=1):
        w = float(s["weight"])
        if w <= 0:
            continue
        used += w
        cost += w * float(s["price"])
        out.append(
            {
                "filled_steps": i,
                "through": s["label"],
                "capital_used": round(used, 4),
                "capital_idle": round(max(0.0, 1.0 - used), 4),
                "avg_cost": round(cost / used, 4) if used else None,
            }
        )
    return out


def _invalidation(spot: float, supports: list[dict], atr: float) -> dict[str, Any]:
    """사다리 전체가 틀렸다고 인정하는 가격.

    최하단 지지 구간의 아래 경계에서 0.5 ATR 더 내려간 곳으로 잡습니다. 경계에
    딱 붙여두면 정상적인 하루 변동에도 걸립니다 -- ATR 여유를 두는 이유입니다.
    """
    if not supports:
        price = spot - 2.0 * atr
        basis = "지지 구간이 없어 현재가 -2 ATR 로 대체했습니다."
    else:
        deepest = min(supports, key=lambda s: s["low"])
        price = float(deepest["low"]) - 0.5 * atr
        basis = "최하단 지지 구간의 하단 -0.5 ATR (일상적 변동에 걸리지 않도록)"
    price = max(price, 0.0)
    return {
        "price": round(price, 4),
        "basis": basis,
        "distance_pct": round(price / spot - 1, 4) if spot else None,
        "meaning": (
            "이 가격 아래에서는 '되돌림 중 매수'라는 전제가 깨집니다. 추세 전환일 "
            "가능성을 인정하고 사다리를 멈추는 지점입니다."
        ),
    }


# ── 매도 사다리 ───────────────────────────────────────────────────────────
def _exit_ladder(spot: float, resistances: list[dict], horizon: int) -> dict[str, Any]:
    steps: list[dict] = []
    probs = [r.get("touch_prob_21d") for r in resistances]
    usable = [p for p in probs if isinstance(p, int | float)]

    if resistances and len(usable) == len(resistances) and sum(usable) > 0:
        total = sum(usable)
        p_first = float(usable[0])
        # 매수와 달리 '즉시 매도분'을 만들지 않습니다. 보유 자체가 결정이므로,
        # 도달하지 않은 몫은 그대로 남습니다.
        weights = [p_first * (p / total) for p in usable]
        basis = "각 저항 구간의 도달 확률에 비례. 도달하지 않은 몫은 보유로 남습니다."
    else:
        weights = [1.0 / (len(resistances) + 1)] * len(resistances) if resistances else []
        basis = "도달 확률을 계산할 수 없어 균등 분할로 대체했습니다."

    for i, (r, w) in enumerate(zip(resistances, weights, strict=True), start=1):
        steps.append(
            {
                "label": f"{i}차 저항",
                "kind": "limit",
                "price": r["price"],
                "low": r["low"],
                "high": r["high"],
                "weight": round(w, 4),
                "distance_pct": r["distance_pct"],
                "touch_prob_21d": r.get("touch_prob_21d"),
                "confidence": r.get("confidence"),
                "why": " · ".join(r.get("methods", [])) or "근거 미상",
            }
        )

    sold = sum(float(s["weight"]) for s in steps)
    avg_exit = (
        round(sum(float(s["weight"]) * float(s["price"]) for s in steps) / sold, 4)
        if sold > 0
        else None
    )
    return {
        "steps": steps,
        "weighting_basis": basis,
        "weight_sold_if_all_reached": round(sold, 4),
        "weight_still_held": round(max(0.0, 1.0 - sold), 4),
        "avg_exit_if_all_reached": avg_exit,
        "why_split": (
            "처분효과(Odean 1998) -- 사람은 오른 종목을 너무 일찍 팝니다. 미리 "
            "정한 사다리는 그 편향에 대한 사전 약속으로 작동합니다."
        ),
    }


# ── 위험 ─────────────────────────────────────────────────────────────────
def _risk(
    avg_cost: float | None, invalidation: dict, exit_: dict
) -> dict[str, Any]:
    """1R(무효화까지의 손실)과 그로부터 역산한 최대 투입 비중."""
    inv = invalidation.get("price")
    out: dict[str, Any] = {
        "risk_per_position_pct": None,
        "reward_to_risk": None,
        "max_position_for_1pct_account_risk": None,
        "basis": (
            "1R = 평균단가에서 무효화 가격까지의 손실입니다. 계좌의 1% 만 잃겠다면 "
            "투입 비중은 1% / (1R 비율) 을 넘을 수 없습니다. 변동성에 맞춰 노출을 "
            "조절하는 방식의 근거는 Moreira & Muir(2017), Harvey 외(2018) 입니다."
        ),
    }
    if avg_cost is None or inv is None or avg_cost <= 0 or inv >= avg_cost:
        return out

    risk_pct = (avg_cost - inv) / avg_cost
    out["risk_per_position_pct"] = round(risk_pct, 4)
    out["max_position_for_1pct_account_risk"] = round(min(1.0, 0.01 / risk_pct), 4)

    avg_exit = exit_.get("avg_exit_if_all_reached")
    if avg_exit and avg_exit > avg_cost:
        out["reward_to_risk"] = round(
            (avg_exit - avg_cost) / (avg_cost - inv), 2
        )
    return out
