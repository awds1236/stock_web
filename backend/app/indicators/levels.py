"""지지/저항 수준과 이동평균 교차 (골든/데드크로스).

솔직한 근거 표기 -- 이 모듈의 지표들은 이 앱에서 근거가 가장 약한 축입니다:

    * 지지/저항: 학술적 근거가 약합니다. "많은 참여자가 같은 가격대를 본다"는
      자기실현적 효과 가설이 주된 논리이며, 체계적 초과수익의 증거는 빈약합니다.
      이 앱은 **매매 신호가 아니라 차트 참고선**으로만 제공합니다.
    * 골든/데드크로스: 이동평균 교차는 구조적으로 **후행** 신호입니다. 장기
      백테스트 문헌에서 단독 사용 성과는 혼재하며, 추세 확인용 서술 변수로만
      씁니다. 교차가 떴다고 사는 규칙은 이 앱이 검증 없이 권하지 않습니다.

모든 함수는 순수함수이며 단일 종목 시계열(날짜 오름차순)을 받습니다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Level:
    price: float
    kind: str  # "support" | "resistance"  (마지막 종가 기준 상대 위치)
    touches: int  # 해당 가격대를 건드린 피벗 수 (많을수록 시장이 의식한 가격대)
    distance_pct: float  # 마지막 종가 대비 거리 (양수 = 위)


def swing_levels(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    pivot_window: int = 5,
    lookback: int = 250,
    tolerance: float = 0.015,
    max_levels: int = 6,
) -> list[Level]:
    """스윙 고점/저점 클러스터링으로 지지/저항 수준 추출.

    방법:
        1. 좌우 `pivot_window` 봉보다 높은 고점(피벗 고점)과 낮은 저점(피벗
           저점)을 최근 `lookback` 봉에서 수집
        2. 서로 `tolerance`(비율) 이내인 피벗들을 하나의 수준으로 클러스터링
        3. 터치 횟수가 많은 순으로 상위 `max_levels` 개 반환

    해석:
        터치 횟수가 많을수록 시장이 반복적으로 의식한 가격대입니다. 마지막 종가
        아래면 지지 후보, 위면 저항 후보로 분류합니다.

    한계 (UI 에 함께 표시할 것):
        수준을 "뚫으면" 의미가 반전되고, 파라미터(윈도우·허용폭)에 민감합니다.
        매매 신호가 아니라 참고선입니다.
    """
    n = len(close)
    if n < pivot_window * 2 + 1:
        return []

    h = high.iloc[-lookback:].reset_index(drop=True)
    lo = low.iloc[-lookback:].reset_index(drop=True)
    m = len(h)

    pivots: list[float] = []
    w = pivot_window
    for i in range(w, m - w):
        window_h = h.iloc[i - w : i + w + 1]
        window_l = lo.iloc[i - w : i + w + 1]
        if np.isfinite(h.iloc[i]) and h.iloc[i] == window_h.max():
            pivots.append(float(h.iloc[i]))
        if np.isfinite(lo.iloc[i]) and lo.iloc[i] == window_l.min():
            pivots.append(float(lo.iloc[i]))

    if not pivots:
        return []

    # 가격순 정렬 후 tolerance 이내 인접 피벗을 같은 클러스터로 묶습니다.
    pivots.sort()
    clusters: list[list[float]] = [[pivots[0]]]
    for p in pivots[1:]:
        if p <= clusters[-1][-1] * (1 + tolerance):
            clusters[-1].append(p)
        else:
            clusters.append([p])

    last_close = float(close.iloc[-1])
    levels = [
        Level(
            price=float(np.mean(c)),
            kind="support" if np.mean(c) <= last_close else "resistance",
            touches=len(c),
            distance_pct=float(np.mean(c) / last_close - 1),
        )
        for c in clusters
    ]

    # 지지·저항을 균형 있게 고릅니다. 터치 수만으로 자르면 추세 종목에서 한쪽만
    # 남습니다 -- 하락 종목은 과거 피벗이 전부 위에 있어 저항만 6개가 되고,
    # '매수 기회 참고'라는 목적을 못 채웁니다. 한쪽이 부족하면 다른 쪽으로
    # 채우되, 없는 수준을 지어내지는 않습니다.
    def rank(items: list[Level]) -> list[Level]:
        # 터치 수 우선, 동률이면 현재가에 가까운 순
        return sorted(items, key=lambda x: (-x.touches, abs(x.distance_pct)))

    supports = rank([x for x in levels if x.kind == "support"])
    resistances = rank([x for x in levels if x.kind == "resistance"])

    half = max_levels // 2
    chosen = supports[:half] + resistances[: max_levels - half]
    if len(chosen) < max_levels:
        remaining = [x for x in rank(levels) if x not in chosen]
        chosen += remaining[: max_levels - len(chosen)]

    # 화면·차트에서 읽기 쉽도록 가격 오름차순으로 반환
    return sorted(chosen, key=lambda x: x.price)


@dataclass(frozen=True)
class CrossState:
    fast: int
    slow: int
    state: str  # "golden" (단기>장기) | "dead" (단기<장기) | "insufficient"
    last_cross: str | None  # 마지막 교차 종류: "golden" | "dead" | None
    days_since_cross: int | None  # 마지막 교차 후 경과 거래일


def ma_cross(
    close: pd.Series,
    dates: pd.Series | None = None,
    *,
    fast: int = 20,
    slow: int = 60,
) -> CrossState:
    """이동평균 교차 상태.

    골든크로스 = 단기선이 장기선을 상향 돌파, 데드크로스 = 하향 돌파.
    현재 상태(정배열/역배열)와 마지막 교차 시점을 함께 반환합니다 --
    "골든크로스 직후"와 "골든크로스 후 200일"은 전혀 다른 상황이기 때문입니다.
    """
    if len(close) < slow + 1:
        return CrossState(fast, slow, "insufficient", None, None)

    sma_f = close.rolling(fast, min_periods=fast).mean()
    sma_s = close.rolling(slow, min_periods=slow).mean()
    diff = (sma_f - sma_s).dropna()
    if len(diff) < 2:
        return CrossState(fast, slow, "insufficient", None, None)

    sign = np.sign(diff.to_numpy())
    state = "golden" if sign[-1] > 0 else "dead"

    # 부호가 바뀐 마지막 지점을 찾습니다 (0 은 이전 부호 유지로 취급).
    nonzero = sign.copy()
    for i in range(1, len(nonzero)):
        if nonzero[i] == 0:
            nonzero[i] = nonzero[i - 1]
    changes = np.where(nonzero[1:] != nonzero[:-1])[0]
    if len(changes) == 0:
        return CrossState(fast, slow, state, None, None)

    last_idx = int(changes[-1]) + 1
    last_cross = "golden" if nonzero[last_idx] > 0 else "dead"
    days_since = len(diff) - 1 - last_idx
    return CrossState(fast, slow, state, last_cross, days_since)


def recent_cross_tag(
    close: pd.Series, *, fast: int = 20, slow: int = 60, within_days: int = 15
) -> str | None:
    """스크리너용: 최근 within_days 거래일 내 교차가 있으면 그 종류를 반환."""
    c = ma_cross(close, fast=fast, slow=slow)
    if c.days_since_cross is not None and c.days_since_cross <= within_days:
        return c.last_cross
    return None
