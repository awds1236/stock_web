/**
 * 주문 계획 -- 분할 구간을 **실제 주문서**로 바꾸는 계산.
 *
 * 구간과 비중만 있으면 "33.3%씩 3회"에서 멈춥니다. 매매에 쓰려면 그 다음이
 * 필요합니다: 몇 주를, 얼마에, 비용까지 물면 실효 단가가 얼마이고, 무효화
 * 가격에 닿으면 **정확히 얼마를 잃는가**.
 *
 * 왜 브라우저에서 계산하는가:
 *   투자금액은 사용자가 넣는 값이라 서버가 미리 알 수 없고, 정적 배포에는
 *   그때 부를 서버가 아예 없습니다. 그래서 가격·비중·비용률까지는 백엔드가
 *   계산해 스냅샷에 싣고(indicators/ladder.py), 금액에 달린 부분만 여기서
 *   합니다.
 *
 * 비용을 빼놓지 않는 이유:
 *   한국 매도에는 증권거래세가 붙습니다(0.15% 안팎, 연도·시장별로 바뀝니다).
 *   왕복 수수료·슬리피지까지 합치면 손익비 계산이 눈에 띄게 달라지고, 특히
 *   손절 손실은 계산보다 항상 큽니다. 비용을 무시한 주문서는 실제보다
 *   낙관적입니다.
 */

import type { Ladder, LadderPlan } from "@/lib/api";

export type Costs = {
  commission: number;
  slippage: number;
  sell_tax: number;
  max_participation: number;
};

/** 비용률이 없을 때의 보수적 기본값. 없다고 0으로 두면 주문서가 낙관적이 됩니다. */
export const FALLBACK_COSTS: Costs = {
  commission: 0.00015,
  slippage: 0.001,
  sell_tax: 0.0015,
  max_participation: 0.01,
};

export type OrderRow = {
  step: number;
  price: number;
  shares: number;
  amount: number; // 이 회차 실제 투입액 (비용 포함)
  cumShares: number;
  cumAmount: number;
  cumAvgPrice: number; // 비용 제외 평균 체결가
  cumEffectivePrice: number; // 비용 포함 실효 단가
  basis: string;
  touches: number | null;
};

export type OrderPlan = {
  rows: OrderRow[];
  capital: number;
  totalShares: number;
  totalAmount: number; // 비용 포함 총 투입액
  totalFees: number; // 매수 수수료 + 슬리피지
  effectivePrice: number; // 비용 포함 평균 매수단가
  unusedCash: number;
  /** 무효화 가격에 전량 손절했을 때 (매도 비용 포함) */
  stopPrice: number | null;
  stopProceeds: number | null;
  lossAtStop: number | null;
  lossPctOfCapital: number | null;
  /** 분할 매도 계획대로 전량 정리했을 때 (매도 비용 포함) */
  targetPrice: number | null;
  targetProceeds: number | null;
  profitAtTarget: number | null;
  profitPctOfCapital: number | null;
  /** 비용까지 반영한 실제 손익비. ladder.risk 의 R:R 은 비용 전 값입니다. */
  netRR: number | null;
  /** 1회차 주문금액이 5일 평균 거래대금에서 차지하는 비율 */
  participation: number | null;
};

/** 1주를 살 때 실제로 나가는 돈 (수수료 + 슬리피지 포함). */
export function buyCostPerShare(price: number, c: Costs): number {
  return price * (1 + c.commission + c.slippage);
}

/** 1주를 팔 때 실제로 들어오는 돈 (수수료 + 슬리피지 + 거래세 차감). */
export function sellNetPerShare(price: number, c: Costs): number {
  return price * (1 - c.commission - c.slippage - c.sell_tax);
}

/**
 * 분할 매수 계획 + 투자금액 -> 주문서.
 *
 * 수량은 **내림**합니다. 국내외 대부분의 계좌가 정수 주로 체결되고, 올림하면
 * 예산을 넘는 주문서가 나옵니다.
 */
export function buildOrderPlan(
  buy: LadderPlan,
  sell: LadderPlan | undefined,
  capital: number,
  costs: Costs,
  avgDailyValue: number | null,
): OrderPlan {
  const rows: OrderRow[] = [];
  let cumShares = 0;
  let cumAmount = 0;
  let cumGross = 0;

  for (const t of buy.tranches) {
    const budget = capital * t.weight;
    const perShare = buyCostPerShare(t.price, costs);
    const shares = perShare > 0 ? Math.floor(budget / perShare) : 0;
    const amount = shares * perShare;
    cumShares += shares;
    cumAmount += amount;
    cumGross += shares * t.price;
    rows.push({
      step: t.step,
      price: t.price,
      shares,
      amount,
      cumShares,
      cumAmount,
      cumAvgPrice: cumShares > 0 ? cumGross / cumShares : 0,
      cumEffectivePrice: cumShares > 0 ? cumAmount / cumShares : 0,
      basis: t.basis,
      touches: t.touches,
    });
  }

  const totalShares = cumShares;
  const totalAmount = cumAmount;
  const effectivePrice = totalShares > 0 ? totalAmount / totalShares : 0;

  const stopPrice = buy.invalidation;
  const stopProceeds =
    stopPrice !== null && totalShares > 0
      ? sellNetPerShare(stopPrice, costs) * totalShares
      : null;
  const lossAtStop = stopProceeds !== null ? totalAmount - stopProceeds : null;

  const targetPrice = sell?.full_fill_avg_price ?? null;
  const targetProceeds =
    targetPrice !== null && totalShares > 0
      ? sellNetPerShare(targetPrice, costs) * totalShares
      : null;
  const profitAtTarget =
    targetProceeds !== null ? targetProceeds - totalAmount : null;

  return {
    rows,
    capital,
    totalShares,
    totalAmount,
    totalFees: totalAmount - cumGross,
    effectivePrice,
    unusedCash: capital - totalAmount,
    stopPrice,
    stopProceeds,
    lossAtStop,
    lossPctOfCapital: lossAtStop !== null && capital > 0 ? lossAtStop / capital : null,
    targetPrice,
    targetProceeds,
    profitAtTarget,
    profitPctOfCapital:
      profitAtTarget !== null && capital > 0 ? profitAtTarget / capital : null,
    netRR:
      lossAtStop !== null && lossAtStop > 0 && profitAtTarget !== null
        ? profitAtTarget / lossAtStop
        : null,
    participation:
      avgDailyValue && avgDailyValue > 0 && rows.length > 0
        ? rows[0].amount / avgDailyValue
        : null,
  };
}

/**
 * 리스크 기준 포지션 사이징.
 *
 * "계좌의 N% 까지만 잃겠다"를 정하면 투입 금액이 **산수로 정해집니다.** 감으로
 * 정하는 것보다 나은 유일한 이유는, 종목마다 무효화까지의 거리가 다르기
 * 때문입니다 -- 손절 폭이 넓은 종목에 같은 금액을 넣으면 같은 %를 잃지 않습니다.
 *
 *   투입금액 = 감수 손실액 / (투입 1원당 손실 비율)
 *
 * 무효화 가격이 없거나 손실 비율이 0 이하면 계산할 수 없습니다(그때는 null).
 */
export function capitalForRisk(
  buy: LadderPlan,
  account: number,
  riskPct: number,
  costs: Costs,
): number | null {
  const stop = buy.invalidation;
  const avg = buy.full_fill_avg_price;
  if (stop === null || avg === null || !(account > 0) || !(riskPct > 0)) return null;

  const inPerShare = buyCostPerShare(avg, costs);
  const outPerShare = sellNetPerShare(stop, costs);
  const lossPerShare = inPerShare - outPerShare;
  if (!(lossPerShare > 0) || !(inPerShare > 0)) return null;

  const lossFraction = lossPerShare / inPerShare; // 투입 1원당 손실
  return (account * riskPct) / lossFraction;
}

export function costsOf(ladder: Ladder | null | undefined): Costs {
  return (ladder?.costs as Costs | undefined) ?? FALLBACK_COSTS;
}
