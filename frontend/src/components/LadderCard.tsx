"use client";

import { pct, type Ladder, type LadderPlan, type Tranche } from "@/lib/api";
import { useState } from "react";

/**
 * 분할 매수·매도 구간.
 *
 * 화면에 "지지 후보 68,400 (터치 4회)"만 떠 있으면 그 다음이 비어 있습니다 --
 * 몇 회로 나눌지, 각 회차에 얼마를 실을지, 다 채워지면 평균단가가 얼마가
 * 되는지를 사용자가 손으로 계산해야 했습니다. 이 카드는 그 산술을 대신합니다.
 *
 * **추천이 아닙니다.** 이 앱은 검증되지 않은 매매 규칙을 권하지 않습니다.
 * 여기서 하는 일은 이미 계산된 수준을 가격순으로 늘어놓고 비중을 배분해
 * 누적 평균단가를 계산하는 것뿐이며, 근거가 되는 지지/저항 자체가 이 앱에서
 * 가장 약한 지표라는 사실은 그대로입니다. 그래서:
 *
 *   - 회차의 근거를 배지로 구분합니다. 지지/저항에서 나온 회차와, 수준이
 *     모자라 변동성으로 채운 회차는 **같은 것이 아닙니다.**
 *   - 한계 문구를 접을 수 없게 표 아래에 고정합니다.
 */
export function LadderCard({ ladder, currency }: { ladder: Ladder; currency?: string | null }) {
  const [weighting, setWeighting] = useState("equal");
  const buy = ladder.buy?.[weighting];
  const sell = ladder.sell?.[weighting];

  if (!buy && !sell) return null;

  return (
    <>
      <h3>분할 매수·매도 구간</h3>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="muted" style={{ fontSize: 12 }}>
            {ladder.steps}분할 · 현재가 {fmt(buy?.last_close ?? sell?.last_close ?? null)}
            {currency ? ` ${currency}` : ""}
            {ladder.atr_14 !== null ? ` · ATR(14) ${fmt(ladder.atr_14)}` : ""}
          </span>
          <label className="row" style={{ gap: 6, fontSize: 12 }}>
            비중 배분
            <select
              value={weighting}
              onChange={(e) => setWeighting(e.target.value)}
              style={{ height: 26, fontSize: 12 }}
              title="어느 쪽이 낫다는 근거는 이 앱에 없습니다. 평균단가가 어떻게 달라지는지 비교용입니다."
            >
              {/* 비율 문구는 회차 수에서 만듭니다. "1:2:3" 을 박아두면 4·5분할에서
                  틀린 설명이 됩니다. */}
              <option value="equal">균등 ({ratio(ladder.steps, "equal")})</option>
              <option value="pyramid">뒤가중 ({ratio(ladder.steps, "pyramid")})</option>
            </select>
          </label>
        </div>
      </div>

      <div className="grid grid-2">
        {buy && (
          <PlanTable
            plan={buy}
            title="분할 매수 구간"
            subtitle="현재가 아래 지지 후보부터 가까운 순"
            avgLabel="평균 매수단가"
          />
        )}
        {sell && (
          <PlanTable
            plan={sell}
            title="분할 매도 구간"
            subtitle="현재가 위 저항 후보부터 가까운 순"
            avgLabel="평균 매도단가"
          />
        )}
      </div>

      <div className="banner warn">
        <strong>이 표를 읽는 법</strong>
        {ladder.caveat}
      </div>
    </>
  );
}

function PlanTable({
  plan,
  title,
  subtitle,
  avgLabel,
}: {
  plan: LadderPlan;
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
      <div className="caveat" style={{ marginTop: 2 }}>{subtitle}</div>

      {/* 6칸짜리 표가 반폭 카드에 들어갑니다. 좁은 화면에서 마지막 칸(근거
          배지)이 카드 밖으로 잘렸으므로, 표만 따로 가로 스크롤시킵니다. */}
      <div style={{ marginTop: 8, overflowX: "auto" }}>
      <table>
        <thead>
          <tr>
            <th style={{ whiteSpace: "nowrap" }}>회차</th>
            <th className="num">가격</th>
            <th className="num">현재가 대비</th>
            <th className="num">비중</th>
            <th className="num">누적 {avgLabel}</th>
            <th>근거</th>
          </tr>
        </thead>
        <tbody>
          {plan.tranches.map((t) => (
            <tr key={t.step}>
              {/* nowrap 이 없으면 좁은 칸에서 "1 / 회 / 차" 로 세로로 쪼개집니다. */}
              <td style={{ whiteSpace: "nowrap" }}>{t.step}회차</td>
              <td className="num">{fmt(t.price)}</td>
              <td className={`num ${buying ? "neg" : "pos"}`}>{pct(t.distance_pct, 1)}</td>
              {/* 소수 한 자리. 정수로 자르면 3등분이 33+33+33=99% 로 보입니다. */}
              <td className="num">{(t.weight * 100).toFixed(1)}%</td>
              <td className="num">
                {fmt(t.avg_price)}
                <span className="muted" style={{ marginLeft: 4, fontSize: 11 }}>
                  {pct(t.avg_vs_close_pct, 1)}
                </span>
              </td>
              <td>
                <BasisBadge tranche={t} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <div className="caveat" style={{ marginTop: 8 }}>
        {plan.tranches.length}회차가 모두 체결되면 {avgLabel}는{" "}
        <strong>{fmt(plan.full_fill_avg_price)}</strong> (현재가 대비{" "}
        {pct(plan.full_fill_avg_vs_close_pct, 1)})입니다.
      </div>
      {plan.invalidation !== null && (
        <div className="caveat">
          {buying ? (
            <>
              <strong>{fmt(plan.invalidation)}</strong> ({pct(plan.invalidation_pct, 1)}) 아래로
              내려가면 이 구간들이 근거로 삼은 지지 후보가 전부 뚫린 것입니다 — 계획의 전제가
              사라진 상태이지 &ldquo;더 싸게 살 기회&rdquo;가 아닙니다.
            </>
          ) : (
            <>
              <strong>{fmt(plan.invalidation)}</strong> ({pct(plan.invalidation_pct, 1)}) 위로
              올라가면 이 구간들이 근거로 삼은 저항 후보가 전부 뚫린 것입니다 — 남은 비중의
              매도 근거가 사라집니다.
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
 * 지지선을 있는 것처럼 읽게 됩니다. 그래서 후자는 '멈춤' 스타일 배지로
 * 눈에 띄게 다르게 표시합니다.
 */
function BasisBadge({ tranche }: { tranche: Tranche }) {
  if (tranche.basis === "level") {
    return (
      <span
        className="badge"
        style={{ whiteSpace: "nowrap" }}
        title="스윙 고저점 클러스터에서 나온 지지/저항 수준입니다. 표 머리의 '지지/저항 N' 이 이 회차 수입니다."
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
    maximumFractionDigits: v >= 1000 ? 0 : 2,
  });
}
