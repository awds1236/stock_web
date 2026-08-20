"""분할 매수·매도 구간 계산.

지지/저항 수준을 **실제로 쓸 수 있는 형태**로 바꾸는 계산입니다. 화면에
"지지 후보 68,400 (터치 4회)"만 떠 있으면 그 다음에 무엇을 해야 하는지가
비어 있습니다 -- 몇 회로 나눌지, 각 회차에 얼마를 실을지, 다 채워지면
평균단가가 얼마가 되는지는 전부 사용자가 손으로 계산해야 했습니다.

**이것은 매매 신호가 아닙니다.** 이 모듈이 하는 일은 산술뿐입니다:

    이미 계산된 수준들을 가격순으로 늘어놓고 -> 비중을 배분하고 ->
    회차별 누적 평균단가를 계산한다

어느 가격에 사라거나 팔라고 말하지 않습니다. "이 계획대로 전부 체결되면
평균단가가 이렇게 된다"는 사실만 계산합니다. 근거가 되는 지지/저항 자체가
학술적으로 약한 참고선이라는 한계는 그대로 남아 있으며(indicators/levels.py
의 모듈 주석), 분할로 나눈다고 그 약함이 사라지지 않습니다.

분할이 실제로 바꾸는 것은 **한 가격에 전부 거는 것보다 진입가의 분산이
줄어든다**는 점 하나입니다. 기대수익을 높이지 않으며, 하락이 이어지면 더 많은
금액이 물립니다 -- 그 사실을 화면에 함께 적습니다.

수준이 모자랄 때:
    지지 후보가 2개뿐인데 3분할을 요구하면, 없는 지지선을 지어내는 대신
    **변동성(ATR) 기준 등간격**으로 채우고 그 회차의 근거를 `volatility` 로
    표시합니다. 근거의 종류가 다르면 화면에서도 달라야 합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 최소 분할 수. 2분할은 '분할'이라 부르기 민망하고, 3분할이면 하단 구간을
# 남겨둘 여지가 생깁니다.
MIN_STEPS = 3
MAX_STEPS = 5
DEFAULT_STEPS = 3

# 비중 배분 방식.
#
#   equal   -- 회차마다 같은 금액. 아무 가정도 하지 않습니다.
#   pyramid -- 뒤 회차(매수는 더 싼 가격, 매도는 더 비싼 가격)에 더 싣습니다.
#              1:2:3 형태로 정규화합니다.
#
# **어느 쪽이 낫다는 근거는 이 앱에 없습니다.** 두 방식이 평균단가를 어떻게
# 바꾸는지를 보여줄 뿐이며, 화면에서 골라 비교하라고 둘 다 계산합니다.
WEIGHTINGS = ("equal", "pyramid")


@dataclass(frozen=True)
class Tranche:
    step: int  # 1회차, 2회차 ...
    price: float
    distance_pct: float  # 현재가 대비 (매수는 음수, 매도는 양수)
    weight: float  # 이 회차 비중 (전체 합 = 1.0)
    cum_weight: float
    avg_price: float  # 이 회차까지 체결됐다고 가정한 누적 평균단가
    avg_vs_close_pct: float  # 그 평균단가가 현재가보다 얼마나 유리한가
    basis: str  # "level" (지지/저항 클러스터) | "volatility" (ATR 등간격)
    touches: int | None  # basis="level" 일 때 그 수준을 건드린 피벗 수


@dataclass(frozen=True)
class LadderPlan:
    side: str  # "buy" | "sell"
    weighting: str
    last_close: float
    tranches: list[Tranche] = field(default_factory=list)
    # 계획의 근거가 무효가 되는 가격. 매수면 마지막 구간보다 더 아래,
    # 매도면 마지막 구간보다 더 위입니다.
    invalidation: float | None = None
    invalidation_pct: float | None = None
    # 전 회차가 다 체결됐을 때의 평균단가 (= 마지막 tranche 의 avg_price)
    full_fill_avg_price: float | None = None
    full_fill_avg_vs_close_pct: float | None = None
    n_level_based: int = 0
    n_volatility_based: int = 0


def _weights(n: int, weighting: str) -> list[float]:
    if n <= 0:
        return []
    if weighting == "pyramid":
        raw = [float(i + 1) for i in range(n)]
    else:
        raw = [1.0] * n
    total = sum(raw)
    return [w / total for w in raw]


def _step_size(last_close: float, atr_value: float | None) -> float:
    """구간 간 최소 간격 (가격 단위).

    ATR 을 쓰는 이유: 간격을 퍼센트로 고정하면 저변동 종목에서는 너무 멀고
    고변동 종목에서는 너무 촘촘해집니다. 다만 ATR 이 비정상적으로 작을 때를
    대비해 현재가의 2% 를 하한으로 둡니다 -- 0.3% 간격의 '3분할'은 사실상
    한 가격에 거는 것과 같습니다.
    """
    floor = last_close * 0.02
    if atr_value is None or not (atr_value > 0):
        return floor
    return max(float(atr_value), floor)


def build_plan(
    side: str,
    last_close: float,
    levels,
    *,
    atr_value: float | None = None,
    steps: int = DEFAULT_STEPS,
    weighting: str = "equal",
) -> LadderPlan:
    """한 방향(매수 또는 매도)의 분할 구간.

    Args:
        levels: `indicators.levels.Level` 목록 (지지·저항이 섞여 있어도 됩니다).
        atr_value: 최근 ATR. 구간 간 최소 간격과 부족분 채우기에 씁니다.
        steps: 분할 횟수. MIN_STEPS 아래로는 내려가지 않습니다.
    """
    steps = max(MIN_STEPS, min(MAX_STEPS, int(steps)))
    if weighting not in WEIGHTINGS:
        weighting = "equal"
    if not (last_close > 0):
        return LadderPlan(side=side, weighting=weighting, last_close=last_close)

    buying = side == "buy"
    gap = _step_size(last_close, atr_value)

    # 후보 수준: 매수는 현재가 아래(가까운 것부터), 매도는 위(가까운 것부터).
    wanted_kind = "support" if buying else "resistance"
    candidates = [
        x
        for x in levels
        if x.kind == wanted_kind and (x.price < last_close if buying else x.price > last_close)
    ]
    candidates.sort(key=lambda x: x.price, reverse=buying)

    prices: list[tuple[float, str, int | None]] = []  # (가격, 근거, 터치수)
    frontier = last_close
    for lvl in candidates:
        if len(prices) >= steps:
            break
        # 직전 구간(첫 회차는 현재가)에서 최소 간격만큼 떨어져 있어야 합니다.
        # 붙어 있는 두 수준에 각각 회차를 배정하면 '분할'이 이름뿐이 됩니다.
        far_enough = (frontier - lvl.price) >= gap if buying else (lvl.price - frontier) >= gap
        if not far_enough:
            continue
        prices.append((float(lvl.price), "level", int(lvl.touches)))
        frontier = float(lvl.price)

    # 수준이 모자라면 변동성 등간격으로 채웁니다. 없는 지지선을 지어내는 대신
    # 근거의 종류를 바꿔 표시합니다.
    while len(prices) < steps:
        nxt = frontier - gap if buying else frontier + gap
        if nxt <= 0:
            break
        prices.append((float(nxt), "volatility", None))
        frontier = nxt

    if not prices:
        return LadderPlan(side=side, weighting=weighting, last_close=last_close)

    weights = _weights(len(prices), weighting)
    tranches: list[Tranche] = []
    cum_w = 0.0
    cum_cost = 0.0
    for i, ((price, basis, touches), w) in enumerate(zip(prices, weights, strict=True), start=1):
        cum_w += w
        cum_cost += w * price
        avg = cum_cost / cum_w
        tranches.append(
            Tranche(
                step=i,
                price=round(price, 4),
                distance_pct=round(price / last_close - 1, 6),
                # 비중은 **반올림하지 않습니다.** 6자리로 자르면 3등분의 합이
                # 0.999999 가 되고, 화면이 그것을 33%+33%+33%=99% 로 보여줍니다.
                # 가격은 표시용이라 반올림해도 되지만 비중은 합이 곧 계약입니다.
                weight=w,
                cum_weight=cum_w,
                avg_price=round(avg, 4),
                avg_vs_close_pct=round(avg / last_close - 1, 6),
                basis=basis,
                touches=touches,
            )
        )

    last_price = tranches[-1].price
    invalidation = last_price - gap if buying else last_price + gap
    invalidation = max(invalidation, 0.0) or None

    return LadderPlan(
        side=side,
        weighting=weighting,
        last_close=round(last_close, 4),
        tranches=tranches,
        invalidation=round(invalidation, 4) if invalidation else None,
        invalidation_pct=(
            round(invalidation / last_close - 1, 6) if invalidation else None
        ),
        full_fill_avg_price=tranches[-1].avg_price,
        full_fill_avg_vs_close_pct=tranches[-1].avg_vs_close_pct,
        n_level_based=sum(1 for t in tranches if t.basis == "level"),
        n_volatility_based=sum(1 for t in tranches if t.basis == "volatility"),
    )


# 별표(**)를 쓰지 않습니다. 이 문자열은 화면의 배너에 **평문으로** 들어가고
# (마크다운 렌더러를 타지 않습니다), 실제 배포 화면에 "**매매 신호가 아니라
# 산술**" 이 별표째 노출됐습니다.
LADDER_CAVEAT = (
    "분할 구간은 매매 신호가 아니라 산술입니다. 근거가 되는 지지/저항은 이 "
    "앱에서 학술적 근거가 가장 약한 지표이며(뚫리면 의미가 반전됩니다), 분할로 "
    "나눈다고 그 약함이 사라지지 않습니다. 분할이 실제로 바꾸는 것은 진입가의 "
    "분산이 줄어든다는 점 하나이며, 기대수익을 높이지 않습니다 -- 하락이 이어지면 "
    "더 많은 금액이 물립니다. 비중 배분 방식(균등 / 뒤가중 -- 매수는 더 싼 회차, "
    "매도는 더 비싼 회차에 더 싣는 방식) 중 어느 쪽이 낫다는 근거도 이 앱에는 "
    "없습니다."
)


def build_ladders(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    levels,
    *,
    steps: int = DEFAULT_STEPS,
) -> dict:
    """매수·매도 × 비중방식 조합의 분할 계획 전부.

    두 비중 방식을 모두 계산해 돌려주는 이유는, 화면에서 전환할 때마다
    서버를 다시 부르지 않기 위해서입니다 -- 정적 배포에는 부를 서버가
    아예 없습니다.
    """
    from app.indicators import price as px

    if close.empty:
        return {"steps": steps, "buy": {}, "sell": {}, "caveat": LADDER_CAVEAT}

    last_close = float(close.iloc[-1])
    atr_series = px.atr(high, low, close)
    atr_value = (
        float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else None
    )

    out: dict = {
        "steps": max(MIN_STEPS, min(MAX_STEPS, int(steps))),
        "atr_14": round(atr_value, 4) if atr_value is not None else None,
        "caveat": LADDER_CAVEAT,
    }
    for side in ("buy", "sell"):
        out[side] = {
            w: _plan_dict(
                build_plan(
                    side,
                    last_close,
                    levels,
                    atr_value=atr_value,
                    steps=steps,
                    weighting=w,
                )
            )
            for w in WEIGHTINGS
        }
    return out


def _plan_dict(plan: LadderPlan) -> dict:
    return {
        "side": plan.side,
        "weighting": plan.weighting,
        "last_close": plan.last_close,
        "invalidation": plan.invalidation,
        "invalidation_pct": plan.invalidation_pct,
        "full_fill_avg_price": plan.full_fill_avg_price,
        "full_fill_avg_vs_close_pct": plan.full_fill_avg_vs_close_pct,
        "n_level_based": plan.n_level_based,
        "n_volatility_based": plan.n_volatility_based,
        "tranches": [
            {
                "step": t.step,
                "price": t.price,
                "distance_pct": t.distance_pct,
                "weight": t.weight,
                "cum_weight": t.cum_weight,
                "avg_price": t.avg_price,
                "avg_vs_close_pct": t.avg_vs_close_pct,
                "basis": t.basis,
                "touches": t.touches,
            }
            for t in plan.tranches
        ],
    }
