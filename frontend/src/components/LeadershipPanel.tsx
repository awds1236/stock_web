"use client";

import type { Leadership } from "@/lib/api";
import { num, pct } from "@/lib/api";

/**
 * 섹터 주도권과 순환.
 *
 * 이 화면의 설계 원칙은 **근거 강도를 시각적으로 분리하는 것**입니다. "지금
 * 무엇이 이끄는가"는 관측이고, "다음은 어디인가"는 추론입니다. 둘을 같은 표에
 * 나란히 놓으면 사용자는 같은 무게로 읽습니다. 그래서 층마다 배지를 붙이고,
 * 추론 층은 검증이 통과했을 때에만 내용이 채워집니다.
 *
 * 근거가 없을 때 비워두는 것이 이 화면의 핵심 기능입니다 -- 빈 화면이
 * 실패처럼 보이지 않도록, 왜 비었는지를 항상 문장으로 씁니다.
 */
export function LeadershipPanel({ data }: { data: Leadership }) {
  const { leaders, persistence, lead_lag: ll, rotation, market_regime: regime } = data;

  return (
    <>
      <h3>
        지금 시장을 이끄는 업종 <Tier kind="관측" />
      </h3>
      {leaders.available ? (
        <>
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>업종</th>
                  <th className="num">20일 초과</th>
                  <th className="num">60일 초과</th>
                  <th className="num">breadth</th>
                  <th className="num">시장 상회 비율</th>
                  <th className="num">종목수</th>
                </tr>
              </thead>
              <tbody>
                {leaders.leading.map((r) => (
                  <tr key={r.sector}>
                    <td>
                      <strong>{r.sector}</strong>
                      {narrowLeadership(r.breadth, r.participation_20d) && (
                        <div className="neg" style={{ fontSize: 11 }}>
                          소수 종목이 끌어올린 주도
                        </div>
                      )}
                    </td>
                    <td className={`num ${cls(r.excess_20d)}`}>{pct(r.excess_20d)}</td>
                    <td className={`num ${cls(r.excess_60d)}`}>{pct(r.excess_60d)}</td>
                    <td className="num">{pct(r.breadth, 0)}</td>
                    <td className="num">{pct(r.participation_20d, 0)}</td>
                    <td className="num">{r.n_constituents}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="caveat">{leaders.score_note}</div>
          </div>

          {leaders.lagging.length > 0 && (
            <details className="card" style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer" }}>뒤처진 업종</summary>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  {leaders.lagging.map((r) => (
                    <tr key={r.sector}>
                      <td>{r.sector}</td>
                      <td className={`num ${cls(r.excess_20d)}`}>{pct(r.excess_20d)}</td>
                      <td className="num">breadth {pct(r.breadth, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      ) : (
        <div className="card">
          <div className="muted">{leaders.reason}</div>
        </div>
      )}

      <h3>
        이 장세의 성격 <Tier kind="관측" />
      </h3>
      <div className="grid grid-4">
        <Metric
          label="상위 5종목 제외 시"
          value={pct(regime.concentration?.ex_top5_ret_20d)}
          note={`전체 20일 ${pct(regime.concentration?.market_ret_20d)} — 차이가 크면 소수 종목 장세`}
        />
        <Metric
          label="종목 간 분산 (20일)"
          value={pct(regime.dispersion_20d)}
          note="표준편차. 낮으면 무엇을 골라도 비슷합니다"
        />
        <Metric
          label="평균 쌍상관 (60일)"
          value={num(regime.avg_pair_correlation_60d, 2)}
          note={corrLabel(regime.avg_pair_correlation_60d)}
        />
        <Metric
          label="유니버스"
          value={`${data.n_universe}종목`}
          note={`${data.level === "industry" ? "세분류" : "대분류"} 기준 · ${data.as_of}`}
        />
      </div>
      {regime.caveat && <div className="caveat">{regime.caveat}</div>}

      <h3>
        이 주도권은 이어지는가 <Tier kind="측정" />
      </h3>
      <div className="card">
        {persistence.available ? (
          <>
            <div style={{ fontSize: 15, marginBottom: 8 }}>
              <strong>{persistence.verdict}</strong>
            </div>
            <table>
              <tbody>
                <tr>
                  <td className="muted">
                    순위상관 (직전 {persistence.lookback_days}일 순위 → 이후{" "}
                    {persistence.horizon_days}일 수익)
                  </td>
                  <td className="num">{num(persistence.rank_ic, 3)}</td>
                </tr>
                <tr>
                  <td className="muted">t 값</td>
                  <td className={`num ${tTone(persistence.t_stat)}`}>
                    {num(persistence.t_stat, 2)}
                  </td>
                </tr>
                <tr>
                  <td className="muted">상위 1/3 − 하위 1/3 (기간당 초과)</td>
                  <td className="num">{pct(persistence.top_minus_bottom)}</td>
                </tr>
                <tr>
                  <td className="muted">표본 기간 수</td>
                  <td className="num">{persistence.n_periods}</td>
                </tr>
              </tbody>
            </table>
            <div className="caveat">{persistence.caveat}</div>
          </>
        ) : (
          <div className="muted">{persistence.reason}</div>
        )}
      </div>

      <h3>
        관련 업종 — 무엇이 무엇을 선행했는가 <Tier kind="추론" />
      </h3>
      <div className="card">
        {ll.available ? (
          <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{ll.verdict}</strong>
              <span className="muted">
                순서쌍 {ll.n_pairs_tested}개 검정 · 문턱 |r| ≥{" "}
                {num(ll.significance_threshold, 3)}
              </span>
            </div>
            <table style={{ marginTop: 8 }}>
              <thead>
                <tr>
                  <th>선행</th>
                  <th>후행</th>
                  <th className="num">주간 상관</th>
                  <th>판정</th>
                </tr>
              </thead>
              <tbody>
                {(ll.pairs ?? []).map((p) => (
                  <tr key={`${p.leader}->${p.follower}`}>
                    <td>{p.leader}</td>
                    <td>{p.follower}</td>
                    <td className="num">{num(p.corr, 3)}</td>
                    <td className={p.significant ? "pos" : "muted"}>
                      {p.significant ? "보정 후 유의" : "문턱 미달"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="caveat">{ll.method}</div>
            <div className="caveat">{ll.caveat}</div>
          </>
        ) : (
          <div className="muted">{ll.reason}</div>
        )}
      </div>

      <h3>
        앞으로 주목받을 수 있는 업종 <Tier kind="추론" />
      </h3>
      {rotation.available ? (
        <>
          <div className="grid grid-2">
            {rotation.candidates.map((c) => (
              <div className="card" key={c.sector}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>{c.sector}</strong>
                  <span className="muted">
                    {c.led_by} 를 {c.direction}으로 후행
                  </span>
                </div>
                <div style={{ marginTop: 6, fontSize: 13 }}>{c.condition}</div>
                <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                  이 업종의 20일 초과수익 {pct(c.excess_20d)} — 아직 덜 움직인 쪽
                </div>
              </div>
            ))}
          </div>
          <div className="card" style={{ marginTop: 8 }}>
            <div className="caveat">{rotation.how_to_read}</div>
            {rotation.persistence_verdict && (
              <div className="caveat">
                주도권 지속성 판정: {rotation.persistence_verdict}
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="banner">
          <strong>후보를 내지 않습니다.</strong>
          {rotation.reason}
          {rotation.what_this_means ? ` ${rotation.what_this_means}` : ""}
        </div>
      )}

      <div className="card" style={{ marginTop: 12 }}>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--muted)" }}>
          {data.caveats.map((c) => (
            <li key={c} style={{ marginBottom: 4 }}>
              {c}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

/** 근거 강도 배지. 관측 / 측정 / 추론을 눈으로 구분하게 합니다. */
function Tier({ kind }: { kind: "관측" | "측정" | "추론" }) {
  const tone = kind === "관측" ? "pos" : kind === "추론" ? "neg" : "muted";
  const title =
    kind === "관측"
      ? "계산된 사실입니다."
      : kind === "측정"
        ? "이 데이터에서 직접 잰 값입니다. 인샘플이며 비용이 없습니다."
        : "추론입니다. 다중검정 보정을 통과한 것만 근거로 씁니다.";
  return (
    <span className={tone} style={{ fontSize: 12, fontWeight: 600 }} title={title}>
      [{kind}]
    </span>
  );
}

function narrowLeadership(breadth: number | null, part: number | null) {
  return (breadth !== null && breadth < 0.5) || (part !== null && part < 0.5);
}

function corrLabel(v: number | null) {
  if (v === null) return "계산 불가";
  if (v > 0.5) return "높음 — 동조화 장세, 분산이 잘 듣지 않습니다";
  if (v < 0.25) return "낮음 — 종목·업종 선택이 결과를 가릅니다";
  return "보통";
}

function tTone(t: number | null | undefined) {
  if (t === null || t === undefined) return "";
  return Math.abs(t) >= 2 ? "pos" : "muted";
}

function cls(v: number | null | undefined) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
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
