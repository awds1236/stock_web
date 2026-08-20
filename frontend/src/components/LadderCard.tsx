"use client";

import { pct, type Ladder, type LadderPlan, type Tranche } from "@/lib/api";
import {
  buildOrderPlan,
  capitalForRisk,
  costsOf,
  type OrderPlan,
} from "@/lib/orderPlan";
import { useEffect, useMemo, useState } from "react";

/**
 * 분할 주문 계획.
 *
 * 구간과 비중만 보여주면 "33.3%씩 3회"에서 멈춥니다. 실제로 주문을 넣으려면
 * 그 다음이 필요합니다 -- 몇 주를, 얼마에, 비용까지 물면 실효 단가가 얼마이고,
 * 무효화 가격에 닿으면 정확히 얼마를 잃는가.
 *
 * 화면의 중심은 **R(리스크 1단위)** 입니다. 어디서 사느냐보다 먼저 정해지는
 * 것이 "틀렸을 때 얼마를 잃느냐"이고, 그 숫자가 없으면 포지션 크기를 감으로
 * 정하게 됩니다. 그래서 손절 손실액을 요약의 첫 칸에 둡니다 -- 목표 수익을
 * 먼저 보면 크기를 키우게 되고, 키운 뒤에 손실액을 보면 이미 늦습니다.
 *
 * 근거의 종류는 계속 구분합니다. 지지/저항에서 나온 회차와 수준이 모자라
 * 변동성으로 채운 회차를 같은 모양으로 두면, 없는 지지선을 있는 것처럼
 * 읽고 그 가격에 실제로 주문을 넣게 됩니다.
 */

const CAPITAL_KEY = "stock_web.capital";
const ACCOUNT_KEY = "stock_web.account";
const RISK_KEY = "stock_web.riskPct";

/** 시장별 기본값. 원화와 달러를 같은 숫자로 두면 한쪽이 무의미해집니다. */
const DEFAULT_CAPITAL: Record<string, number> = { KR: 10_000_000, US: 10_000 };
const DEFAULT_ACCOUNT: Record<string, number> = { KR: 100_000_000, US: 100_000 };

function loadNumber(
  key: string,
  market: string,
  fallback: Record<string, number>,
): number {
  if (typeof window === "undefined") return fallback[market] ?? 0;
  const raw = window.localStorage.getItem(`${key}.${market}`);
  const n = raw === null ? NaN : Number(raw);
  return Number.isFinite(n) && n > 0 ? n : (fallback[market] ?? 0);
}

export function LadderCard({
  ladder,
  market,
  currency,
}: {
  ladder: Ladder;
  market: string;
  currency?: string | null;
}) {
  const [weighting, setWeighting] = useState("equal");
  const [capital, setCapital] = useState(0);
  const [account, setAccount] = useState(0);
  const [riskPct, setRiskPct] = useState(0.02);

  // 입력값은 브라우저에 남깁니다. 종목을 바꿀 때마다 계좌 크기를 다시 넣어야
  // 하면 이 화면은 쓰이지 않습니다.
  useEffect(() => {
    setCapital(loadNumber(CAPITAL_KEY, market, DEFAULT_CAPITAL));
    setAccount(loadNumber(ACCOUNT_KEY, market, DEFAULT_ACCOUNT));
    const raw = window.localStorage.getItem(RISK_KEY);
    const n = raw === null ? NaN : Number(raw);
    if (Number.isFinite(n) && n > 0) setRiskPct(n);
  }, [market]);

  const remember = (key: string, value: number, perMarket = true) => {
    try {
      window.localStorage.setItem(perMarket ? `${key}.${market}` : key, String(value));
    } catch {
      /* 프라이빗 모드 등에서 막힐 수 있습니다. 저장 실패가 계산을 막지는 않습니다. */
    }
  };

  const buy = ladder.buy?.[weighting];
  const sell = ladder.sell?.[weighting];
  const costs = useMemo(() => costsOf(ladder), [ladder]);

  const order: OrderPlan | null = useMemo(
    () =>
      buy && capital > 0
        ? buildOrderPlan(buy, sell, capital, costs, ladder.avg_daily_value ?? null)
        : null,
    [buy, sell, capital, costs, ladder.avg_daily_value],
  );

  const sized = useMemo(
    () => (buy ? capitalForRisk(buy, account, riskPct, costs) : null),
    [buy, account, riskPct, costs],
  );

  if (!buy && !sell) return null;

  const unit = currency === "KRW" ? "원" : currency === "USD" ? "$" : "";

  return (
    <>
      <h3>분할 주문 계획</h3>

      <div className="card">
        <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
          <label className="row" style={{ gap: 6, fontSize: 12 }}>
            투자금액
            <input
              type="number"
              value={capital || ""}
              min={0}
              step={market === "KR" ? 100000 : 100}
              onChange={(e) => {
                const v = Number(e.target.value);
                setCapital(v);
                remember(CAPITAL_KEY, v);
              }}
              style={{ width: 140 }}
            />
          </label>
          <label className="row" style={{ gap: 6, fontSize: 12 }}>
            계좌 평가금액
            <input
              type="number"
              value={account || ""}
              min={0}
              step={market === "KR" ? 1000000 : 1000}
              onChange={(e) => {
                const v = Number(e.target.value);
                setAccount(v);
                remember(ACCOUNT_KEY, v);
              }}
              style={{ width: 140 }}
            />
          </label>
          <label className="row" style={{ gap: 6, fontSize: 12 }}>
            감수 손실
            <select
              value={riskPct}
              onChange={(e) => {
                const v = Number(e.target.value);
                setRiskPct(v);
                remember(RISK_KEY, v, false);
              }}
              style={{ height: 30, fontSize: 12 }}
            >
              {[0.005, 0.01, 0.02, 0.03, 0.05].map((r) => (
                <option key={r} value={r}>
                  계좌의 {(r * 100).toFixed(1)}%
                </option>
              ))}
            </select>
          </label>
          <label className="row" style={{ gap: 6, fontSize: 12 }}>
            비중 배분
            <select
              value={weighting}
              onChange={(e) => setWeighting(e.target.value)}
              style={{ height: 30, fontSize: 12 }}
            >
              <option value="equal">균등 ({ratio(ladder.steps, "equal")})</option>
              <option value="pyramid">뒤가중 ({ratio(ladder.steps, "pyramid")})</option>
            </select>
          </label>
        </div>

        {sized !== null && (
          <div className="row" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
            <button
              onClick={() => {
                const v = Math.floor(sized);
                setCapital(v);
                remember(CAPITAL_KEY, v);
              }}
            >
              리스크 {(riskPct * 100).toFixed(1)}% 에 맞추기 → {fmt(Math.floor(sized))}
              {unit}
            </button>
            <span className="muted" style={{ fontSize: 12 }}>
              무효화 가격에 전량 손절해도 계좌의 {(riskPct * 100).toFixed(1)}% 만 잃는
              금액입니다.
            </span>
          </div>
        )}

        <div className="caveat" style={{ marginTop: 8 }}>
          {ladder.steps}분할 · 현재가 {fmt(buy?.last_close ?? sell?.last_close ?? null)}
          {unit}
          {ladder.atr_14 !== null ? ` · ATR(14) ${fmt(ladder.atr_14)}` : ""} · 비용
          가정 수수료 {(costs.commission * 100).toFixed(3)}% · 슬리피지{" "}
          {(costs.slippage * 100).toFixed(2)}% · 매도세{" "}
          {(costs.sell_tax * 100).toFixed(3)}%
        </div>
      </div>

      {order && <RiskSummary order={order} unit={unit} account={account} />}

      <div className="grid grid-2">
        {buy && (
          <PlanTable
            plan={buy}
            order={order}
            title="분할 매수"
            subtitle="현재가 아래 지지 후보부터 가까운 순"
            avgLabel="평균 매수단가"
          />
        )}
        {sell && (
          <PlanTable
            plan={sell}
            order={null}
            title="분할 매도"
            subtitle="현재가 위 저항 후보부터 가까운 순"
            avgLabel="평균 매도단가"
          />
        )}
      </div>

      <div className="caveat">{ladder.caveat}</div>
    </>
  );
}

/** 손익 요약 -- 이 화면에서 가장 먼저 읽어야 하는 줄. */
function RiskSummary({
  order,
  unit,
  account,
}: {
  order: OrderPlan;
  unit: string;
  account: number;
}) {
  // **실현되는** 계좌 대비 리스크. 목표치가 아니라 이 주문서의 실제 값입니다.
  //
  // 수량을 내림하기 때문에 "리스크 2%에 맞추기" 로 계산한 금액도 실제로는
  // 2% 를 조금 밑돕니다(주가가 비쌀수록 차이가 큽니다 -- 3회차면 최대 3주만큼
  // 덜 삽니다). 목표치를 그대로 적으면 화면이 거짓말을 하므로 실현값을
  // 씁니다. 내림은 초과가 아니라 미달 방향이라 안전한 쪽으로만 어긋납니다.
  const lossPctOfAccount =
    order.lossAtStop !== null && account > 0 ? order.lossAtStop / account : null;
  return (
    <div className="grid grid-4">
      <Metric
        label="손절 시 손실"
        value={order.lossAtStop === null ? "—" : `-${fmt(order.lossAtStop)}${unit}`}
        note={
          order.stopPrice === null
            ? "무효화 가격 없음"
            : `${fmt(order.stopPrice)}${unit} 전량 · 투입액의 ${pct(
                order.lossPctOfCapital,
                1,
              )}${
                lossPctOfAccount !== null
                  ? ` · 계좌의 ${pct(lossPctOfAccount, 2)}`
                  : ""
              }`
        }
        tone="neg"
      />
      <Metric
        label="목표 도달 시 수익"
        value={
          order.profitAtTarget === null ? "—" : `+${fmt(order.profitAtTarget)}${unit}`
        }
        note={
          order.targetPrice === null
            ? "매도 구간 없음"
            : `${fmt(order.targetPrice)}${unit} 전량 · ${pct(
                order.profitPctOfCapital,
                1,
              )}`
        }
        tone="pos"
      />
      <Metric
        label="손익비 (비용 포함)"
        value={order.netRR === null ? "—" : `${order.netRR.toFixed(2)} : 1`}
        note="수수료·슬리피지·매도세를 뺀 실제 비율"
      />
      <Metric
        label="총 매수"
        value={`${order.totalShares.toLocaleString()}주`}
        note={`${fmt(order.totalAmount)}${unit} · 실효 단가 ${fmt(
          order.effectivePrice,
        )}${unit}`}
      />
    </div>
  );
}

function Metric({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: string;
}) {
  return (
    <div className="card metric">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

function PlanTable({
  plan,
  order,
  title,
  subtitle,
  avgLabel,
}: {
  plan: LadderPlan;
  /** 매수 표에만 수량 칸이 붙습니다. 매도 수량은 보유량에 달려 있어 다릅니다. */
  order: OrderPlan | null;
  title: string;
  subtitle: string;
  avgLabel: string;
}) {
  if (plan.tranches.length === 0) {
    return (
      <div className="card">
        <strong>{title}</strong>
        <div className="muted" style={{ marginTop: 8 }}>
          구간을 만들지 못했습니다. 데이터가 짧아 수준이 잡히지 않은 경우입니다.
        </div>
      </div>
    );
  }

  const buying = plan.side === "buy";

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{title}</strong>
        <span className="muted" style={{ fontSize: 11 }}>
          지지/저항 {plan.n_level_based} · 변동성 {plan.n_volatility_based}
        </span>
      </div>
      <div className="caveat" style={{ marginTop: 2 }}>
        {subtitle}
      </div>

      {/* 좁은 화면에서 마지막 칸이 카드 밖으로 잘리므로 표만 가로 스크롤시킵니다. */}
      <div style={{ marginTop: 8, overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              {/* 머리글을 짧게 씁니다. '현재가 대비'·'누적 평균 매수단가'를
                  그대로 두면 6칸이 반폭 카드를 넘겨 근거 배지가 잘립니다. */}
              <th style={{ whiteSpace: "nowrap" }}>회차</th>
              <th className="num">가격</th>
              <th className="num" title="현재가 대비 거리">
                대비
              </th>
              {order ? (
                <th className="num">주문</th>
              ) : (
                <th className="num">비중</th>
              )}
              <th className="num" title={`누적 ${avgLabel}`}>
                누적단가
              </th>
              <th>근거</th>
            </tr>
          </thead>
          <tbody>
            {plan.tranches.map((t, i) => {
              const row = order?.rows[i];
              return (
                <tr key={t.step}>
                  <td style={{ whiteSpace: "nowrap" }}>{t.step}회차</td>
                  <td className="num">{fmt(t.price)}</td>
                  <td className={`num ${buying ? "neg" : "pos"}`}>
                    {pct(t.distance_pct, 1)}
                  </td>
                  {row ? (
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      {row.shares.toLocaleString()}주
                      <div className="muted" style={{ fontSize: 11 }}>
                        {fmt(row.amount)}
                      </div>
                    </td>
                  ) : (
                    <td className="num">{(t.weight * 100).toFixed(1)}%</td>
                  )}
                  <td className="num">
                    {fmt(row ? row.cumEffectivePrice : t.avg_price)}
                    <div className="muted" style={{ fontSize: 11 }}>
                      {pct(t.avg_vs_close_pct, 1)}
                    </div>
                  </td>
                  <td>
                    <BasisBadge tranche={t} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {order ? (
        <div className="caveat" style={{ marginTop: 8 }}>
          누적 단가는 <strong>비용 포함 실효 단가</strong>입니다. 미체결 잔액{" "}
          {fmt(order.unusedCash)} · 매수 비용 {fmt(order.totalFees)}
          {order.participation !== null && (
            <>
              {" "}
              · 1회차 주문은 5일 평균 거래대금의 {pct(order.participation, 2)}
              {order.participation > 0.01 ? " — 분할 체결을 고려하십시오" : ""}
            </>
          )}
        </div>
      ) : (
        <div className="caveat" style={{ marginTop: 8 }}>
          {plan.tranches.length}회차 전량 정리 시 {avgLabel}{" "}
          <strong>{fmt(plan.full_fill_avg_price)}</strong> (현재가 대비{" "}
          {pct(plan.full_fill_avg_vs_close_pct, 1)})
        </div>
      )}
      {plan.invalidation !== null && (
        <div className="caveat">
          {buying ? (
            <>
              무효화 <strong>{fmt(plan.invalidation)}</strong> (
              {pct(plan.invalidation_pct, 1)}) — 이 아래로 내려가면 근거로 삼은 지지가
              전부 뚫린 것입니다.
            </>
          ) : (
            <>
              돌파 <strong>{fmt(plan.invalidation)}</strong> (
              {pct(plan.invalidation_pct, 1)}) — 이 위로 올라가면 저항이 전부 뚫린
              것이므로 남은 비중을 다시 볼 지점입니다.
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 회차의 근거 배지.
 *
 * 지지/저항에서 나온 회차와 변동성으로 채운 회차를 같은 모양으로 두면, 없는
 * 지지선을 있는 것처럼 읽고 그 가격에 실제로 주문을 넣게 됩니다.
 */
function BasisBadge({ tranche }: { tranche: Tranche }) {
  if (tranche.basis === "level") {
    return (
      <span
        className="badge"
        style={{ whiteSpace: "nowrap" }}
        title="스윙 고저점 클러스터에서 나온 지지/저항 수준입니다."
      >
        터치 {tranche.touches ?? "—"}회
      </span>
    );
  }
  return (
    <span
      className="badge stale"
      style={{ whiteSpace: "nowrap" }}
      title="이 방향의 지지/저항 수준이 모자라 ATR(변동성) 등간격으로 채운 구간입니다. 수준이 아닙니다."
    >
      변동성 등간격
    </span>
  );
}

/** 선택지 라벨의 비중 비율 문구 (예: 3분할 뒤가중 -> "1:2:3"). */
function ratio(steps: number, weighting: string): string {
  const n = Math.max(1, steps);
  return Array.from({ length: n }, (_, i) => (weighting === "pyramid" ? i + 1 : 1)).join(
    ":",
  );
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2,
  });
}
