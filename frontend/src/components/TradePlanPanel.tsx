"use client";

import type { Plan, Regime, Scenario, Zone, Zones } from "@/lib/api";
import { num, pct } from "@/lib/api";

/**
 * 지지·저항 구간과 분할 체결 골격.
 *
 * 이 화면이 AI 서술보다 먼저 오는 이유: **숫자는 여기가 원본**입니다. AI 는
 * 같은 dict 를 받아 문장으로 옮길 뿐이고, 둘이 어긋나면 이 화면이 맞습니다.
 * 그래서 근거·확률·비중을 전부 여기서 먼저 보여줍니다 -- AI 키가 없어도
 * 분할 매수·매도에 필요한 것은 전부 나옵니다.
 *
 * 표기 원칙:
 *   * 지지·저항은 **구간**으로 적습니다. 점으로 적으면 사용자가 그 가격에
 *     지정가를 걸고, 1틱 차이로 놓칩니다.
 *   * 근거가 몇 개 겹쳤는지와 그 근거의 등급을 항상 함께 보여줍니다.
 *   * 확률은 가정이 붙은 계산값이라는 것을 매번 밝힙니다.
 */
export function TradePlanPanel({
  zones,
  plan,
  regime,
  scenarios,
}: {
  zones: Zones;
  plan: Plan;
  regime: Regime;
  scenarios: Scenario[];
}) {
  if (!zones || zones.insufficient) {
    return (
      <div className="card">
        <div className="muted">{zones?.note ?? "가격대를 계산할 수 없습니다."}</div>
      </div>
    );
  }

  return (
    <>
      <h3>지금 국면</h3>
      <div className="grid grid-4">
        <Metric label="추세" value={regime.trend.label} note={regime.trend.basis} />
        <Metric
          label="변동성 국면"
          value={regime.volatility.label}
          note={
            regime.volatility.percentile_2y != null
              ? `최근 2년 중 ${(regime.volatility.percentile_2y * 100).toFixed(0)}번째 백분위`
              : "백분위 계산 불가"
          }
        />
        <Metric
          label="52주 레인지 위치"
          value={
            regime.range_position?.value != null
              ? `${(regime.range_position.value * 100).toFixed(0)}%`
              : "—"
          }
          note={
            regime.range_position
              ? `${fmt(regime.range_position.low)} ~ ${fmt(regime.range_position.high)}`
              : undefined
          }
        />
        <Metric
          label="ATR(14)"
          value={fmt(zones.atr_14)}
          note={
            zones.atr_pct != null
              ? `현재가의 ${(zones.atr_pct * 100).toFixed(2)}% — 구간 폭의 기준`
              : undefined
          }
        />
      </div>
      {regime.trend.caveat && <div className="caveat">{regime.trend.caveat}</div>}

      {/* "3개"라고 못 박지 않습니다. 근거가 겹치는 구간이 그보다 적으면 적은
          대로 보여주는 것이 이 화면의 규칙입니다. */}
      <h3>
        지지·저항 구간 (최대 3+3){" "}
        <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
          — 근거가 겹칠수록 신뢰도가 올라갑니다
        </span>
      </h3>
      <div className="grid grid-2">
        <ZoneColumn title="지지 (아래)" zones={zones.supports} horizon={zones.horizon_days} />
        <ZoneColumn title="저항 (위)" zones={zones.resistances} horizon={zones.horizon_days} />
      </div>
      <div className="caveat">{zones.note}</div>

      <details className="card" style={{ marginTop: 8 }}>
        <summary style={{ cursor: "pointer" }}>
          근거 등급 — 어떤 방법을 얼마나 믿는가
        </summary>
        <table style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>방법</th>
              <th className="num">가중치</th>
              <th>근거</th>
            </tr>
          </thead>
          <tbody>
            {zones.method_notes.map((m) => (
              <tr key={m.method}>
                <td>{m.method}</td>
                <td className="num">{m.evidence_weight.toFixed(1)}</td>
                <td style={{ fontSize: 12 }}>{m.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {plan.available ? (
        <>
          <h3>분할 매수 골격</h3>
          <div className="banner">
            <strong>이것은 매수 추천이 아닙니다.</strong>
            매수할지 여부는 사용자의 판단입니다. 아래는 &quot;이미 사기로 정했다면
            어디에 얼마씩 나눌 것인가&quot;의 산술입니다.
          </div>
          <div className="card">
            <StepTable steps={plan.entry.steps} />
            <div className="caveat">{plan.entry.weighting_basis}</div>
            {plan.entry.prob_no_limit_fill_21d != null && (
              <div className="caveat">
                지정가만 걸어둘 경우 {plan.horizon_days}거래일 안에 한 주도 체결되지
                않을 확률이 <strong>{pct(plan.entry.prob_no_limit_fill_21d, 0)}</strong>{" "}
                입니다. &apos;즉시&apos; 단계는 그 몫입니다.
              </div>
            )}
          </div>

          <h4 style={{ margin: "12px 0 6px" }}>어디까지 체결되느냐에 따른 평균단가</h4>
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>체결 구간</th>
                  <th className="num">투입 비중</th>
                  <th className="num">남는 현금</th>
                  <th className="num">평균단가</th>
                </tr>
              </thead>
              <tbody>
                {plan.entry.partial_fills.map((f) => (
                  <tr key={f.filled_steps}>
                    <td>{f.through}까지</td>
                    <td className="num">{pct(f.capital_used, 0)}</td>
                    <td className="num">{pct(f.capital_idle, 0)}</td>
                    <td className="num">{fmt(f.avg_cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="caveat">
              전량 체결 평균단가만 보면 착시가 생깁니다. 실제로는 앞 단계만 체결된
              채 반등하는 경우가 가장 흔하고, 그때 평균단가는 위 표의 첫 줄에
              가깝습니다.
            </div>
          </div>

          <div className="grid grid-2" style={{ marginTop: 8 }}>
            <div className="card">
              <div className="label">전제가 깨지는 가격 (무효화)</div>
              <div className="value neg" style={{ fontSize: 20 }}>
                {fmt(plan.invalidation.price)}
                <span className="muted" style={{ fontSize: 12, marginLeft: 6 }}>
                  {pct(plan.invalidation.distance_pct)}
                </span>
              </div>
              <div className="note">{plan.invalidation.basis}</div>
              <div className="caveat">{plan.invalidation.meaning}</div>
            </div>
            <div className="card">
              <div className="label">위험 환산</div>
              <table>
                <tbody>
                  <tr>
                    <td className="muted">1R (평균단가→무효화)</td>
                    <td className="num">{pct(plan.risk.risk_per_position_pct)}</td>
                  </tr>
                  <tr>
                    <td className="muted">보상/위험 (전량 도달 가정)</td>
                    <td className="num">
                      {plan.risk.reward_to_risk != null
                        ? `${plan.risk.reward_to_risk.toFixed(2)}배`
                        : "—"}
                    </td>
                  </tr>
                  <tr>
                    <td className="muted">계좌 1% 위험 기준 최대 비중</td>
                    <td className="num">
                      {pct(plan.risk.max_position_for_1pct_account_risk, 1)}
                    </td>
                  </tr>
                </tbody>
              </table>
              <div className="caveat">{plan.risk.basis}</div>
            </div>
          </div>

          <h3>분할 매도 골격</h3>
          <div className="card">
            <StepTable steps={plan.exit.steps} />
            <div className="caveat">{plan.exit.weighting_basis}</div>
            <div className="caveat">
              모든 저항에 도달해도 <strong>{pct(plan.exit.weight_still_held, 0)}</strong>{" "}
              는 팔리지 않은 채 남습니다. 전량 도달 시 평균 매도가는{" "}
              {fmt(plan.exit.avg_exit_if_all_reached)} 입니다.
            </div>
            <div className="caveat">{plan.exit.why_split}</div>
          </div>

          <div className="card" style={{ marginTop: 8 }}>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--muted)" }}>
              {plan.caveats.map((c) => (
                <li key={c} style={{ marginBottom: 4 }}>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : (
        <div className="banner warn">
          <strong>분할 골격을 계산하지 못했습니다</strong>
          {plan?.reason}
        </div>
      )}

      {scenarios.length > 0 && (
        <>
          <h3>앞으로의 조건부 시나리오</h3>
          <div className="grid grid-2">
            {scenarios.map((s) => (
              <div className="card" key={s.name}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>{s.name}</strong>
                  <span className="muted">
                    {s.touch_prob != null ? pct(s.touch_prob, 0) : "—"} /{" "}
                    {s.horizon_days}일
                  </span>
                </div>
                <div style={{ marginTop: 6, fontSize: 13 }}>
                  <span className="muted">조건: </span>
                  {s.trigger}
                </div>
                <div style={{ marginTop: 4, fontSize: 13 }}>{s.then}</div>
                {s.invalidated_by !== "-" && (
                  <div className="caveat">{s.invalidated_by}</div>
                )}
                {s.prob_note && <div className="caveat">{s.prob_note}</div>}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ZoneColumn({
  title,
  zones,
  horizon,
}: {
  title: string;
  zones: Zone[];
  horizon: number;
}) {
  return (
    <div className="card">
      <strong>{title}</strong>
      {zones.length === 0 ? (
        <div className="muted" style={{ marginTop: 8 }}>
          이 방향에는 근거가 겹치는 구간이 없습니다. 없는 수준을 지어내지
          않습니다.
        </div>
      ) : (
        zones.map((z, i) => (
          <div
            key={z.price}
            style={{
              marginTop: 10,
              paddingTop: 10,
              borderTop: i === 0 ? "none" : "1px solid var(--border)",
            }}
          >
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>
                {i + 1}차 · {fmt(z.low)} ~ {fmt(z.high)}
              </strong>
              {/* 거리는 손익이 아니므로 빨강·초록을 쓰지 않습니다. 지지가
                  빨갛게 찍히면 '나쁜 것'으로 읽힙니다. */}
              <span className="muted">{pct(z.distance_pct)}</span>
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              대표가 {fmt(z.price)} · 현재가에서 {num(z.distance_atr, 1)} ATR ·{" "}
              {horizon}일 내 도달 확률{" "}
              <strong>{z.touch_prob_21d != null ? pct(z.touch_prob_21d, 0) : "—"}</strong>
            </div>
            <div style={{ marginTop: 4, fontSize: 12 }}>
              <Badge confidence={z.confidence} /> 근거 {z.n_methods}종:{" "}
              {z.methods.join(" · ")}
            </div>
            <details style={{ marginTop: 4 }}>
              <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--muted)" }}>
                근거 상세
              </summary>
              <ul style={{ margin: "4px 0 0", paddingLeft: 16, fontSize: 12 }}>
                {z.evidence.map((e, k) => (
                  <li key={`${e.method}-${k}`}>
                    {e.method} {fmt(e.price)} — {e.detail}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        ))
      )}
    </div>
  );
}

function Badge({ confidence }: { confidence: string }) {
  const tone =
    confidence === "높음" ? "pos" : confidence === "낮음" ? "neg" : "muted";
  return (
    <span className={tone} style={{ fontWeight: 600 }}>
      [{confidence}]
    </span>
  );
}

function StepTable({ steps }: { steps: { label: string; kind: string; price: number; low: number; high: number; weight: number; distance_pct: number; touch_prob_21d: number | null; why: string }[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>단계</th>
          <th>가격대</th>
          <th className="num">거리</th>
          <th className="num">도달확률</th>
          <th className="num">비중</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((s) => (
          <tr key={s.label}>
            <td>
              {s.label}
              <div className="muted" style={{ fontSize: 11 }}>
                {s.why}
              </div>
            </td>
            <td>
              {s.kind === "market" ? fmt(s.price) : `${fmt(s.low)} ~ ${fmt(s.high)}`}
            </td>
            <td className="num">{pct(s.distance_pct)}</td>
            <td className="num">
              {s.touch_prob_21d != null ? pct(s.touch_prob_21d, 0) : "—"}
            </td>
            <td className="num">
              <strong>{pct(s.weight, 0)}</strong>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="card metric">
      <div className="label">{label}</div>
      <div className="value" style={{ fontSize: 18 }}>
        {value}
      </div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2,
  });
}
