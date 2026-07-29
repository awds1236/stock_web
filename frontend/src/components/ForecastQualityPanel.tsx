"use client";

import type { ForecastQuality } from "@/lib/api";
import { num, pct } from "@/lib/api";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * 예측 품질 패널.
 *
 * 이 컴포넌트는 예측 화면에서 **생략할 수 없습니다.** 보정되지 않은 확률만
 * 보여주면 사용자가 과신하게 되고, 우연보다 약간 나은 수준의 모형에 과신이
 * 더해지면 통상적 베팅 규칙 하에서 장기 성장률이 음수가 됩니다.
 *
 * 타깃별로 **다른 지표**를 보여줍니다. 방향은 skill score, 수익률은 횡단면
 * R²·IC, 변동성은 지속성 기준선 대비. 같은 잣대를 돌려쓰면 잘못된 판정이
 * 나옵니다 -- 무의미한 지표를 대시(—)로 깔아두는 대신 아예 그리지 않습니다.
 */
export function ForecastQualityPanel({
  q,
  target,
}: {
  q: ForecastQuality;
  target: string;
}) {
  const isDirection = target === "direction";
  const skill = q.skill_score;
  const useless = isDirection && skill !== null && skill <= 0;
  const losesToBaseline = !isDirection && q.beats_baseline === false;

  const curve = q.reliability_curve
    .filter((r) => r.predicted !== null && r.observed !== null && r.n > 0)
    .map((r) => ({
      predicted: r.predicted!,
      observed: r.observed!,
      ideal: r.predicted!,
      n: r.n,
    }));

  return (
    <>
      {q.leakage_warning && (
        <div className="banner bad">
          <strong>데이터 누수 의심</strong>
          {q.leakage_warning}
        </div>
      )}

      {useless && (
        <div className="banner warn">
          <strong>이 모형은 현재 쓸모가 없습니다</strong>
          skill score 가 {num(skill, 4)} (0 이하) 입니다. 기저율을 그냥 답하는
          것보다 나을 것이 없다는 뜻입니다. 이 예측을 매매 근거로 삼지 마십시오.
        </div>
      )}

      {losesToBaseline && (
        <div className="banner warn">
          <strong>기준선을 이기지 못했습니다</strong>
          {q.baseline_label ?? "기준선"}(R² {pct(q.baseline_r2, 2)})이 이 모형(R²{" "}
          {pct(q.oos_r2, 2)})보다 낫습니다. 복잡한 모형을 쓸 이유가 없는
          상태이며, 이 예측을 매매 근거로 삼지 마십시오.
        </div>
      )}

      <h3>예측 품질 (아웃오브샘플)</h3>

      {isDirection ? (
        <>
          <div className="grid grid-4">
            <Metric
              label="skill score"
              value={num(skill, 4)}
              note={useless ? "0 이하 = 정보 없음" : "0 초과 = 기저율보다 나음"}
              tone={useless ? "bad" : "good"}
            />
            <Metric
              label="보정 오차 (ECE)"
              value={num(q.ece, 4)}
              note="낮을수록 좋음. 확률이 실제 빈도와 일치하는 정도"
            />
            <Metric
              label="변별력 (resolution)"
              value={num(q.resolution, 5)}
              note="높을수록 좋음. 기저율과 다른 예측을 하는 능력"
            />
            <Metric
              label="신뢰도 오차 (reliability)"
              value={num(q.reliability, 5)}
              note="낮을수록 좋음. 과신하면 커집니다"
            />
          </div>
          <div className="grid grid-4" style={{ marginTop: 12 }}>
            <Metric
              label="Brier 점수"
              value={num(q.brier, 4)}
              note="낮을수록 좋음"
            />
            <Metric
              label="검증 구간"
              value={`${q.n_folds} fold`}
              note={`${q.n_predictions.toLocaleString()} 건 예측`}
            />
          </div>
        </>
      ) : (
        <div className="grid grid-4">
          <Metric
            label="전체 R²"
            value={pct(q.oos_r2, 2)}
            note={
              target === "volatility"
                ? "변동성은 군집성 탓에 수십%가 정상"
                : "시장 드리프트를 맞힌 몫 포함"
            }
          />
          <Metric
            label="횡단면 R² (드리프트 제거)"
            value={pct(q.oos_r2_cross, 3)}
            note="종목 간 우열을 맞힌 몫만. 수익률 문헌 기준은 여기에 적용"
          />
          <Metric
            label="일별 IC (순위상관)"
            value={num(q.mean_daily_ic, 4)}
            note="0.02~0.05 = 실전 수준, 0.10 초과 지속 = 누수 의심"
          />
          <Metric
            label={`${q.baseline_label ?? "기준선"} 대비`}
            value={
              q.beats_baseline === null
                ? "—"
                : q.beats_baseline
                  ? "우세"
                  : "열세"
            }
            note={
              q.baseline_r2 === null
                ? undefined
                : `기준선 R² ${pct(q.baseline_r2, 2)}`
            }
            tone={
              q.beats_baseline === false
                ? "bad"
                : q.beats_baseline
                  ? "good"
                  : undefined
            }
          />
          <Metric
            label="검증 구간"
            value={`${q.n_folds} fold`}
            note={`${q.n_predictions.toLocaleString()} 건 예측`}
          />
        </div>
      )}

      {isDirection && curve.length > 1 && (
        <>
          <h3>신뢰도 곡선</h3>
          <p className="sub">
            대각선에 가까울수록 잘 보정된 것입니다. 점이 대각선 아래에 있으면 그
            구간에서 <strong>과신</strong>하고 있다는 뜻입니다. &quot;정확도
            57%&quot; 같은 단일 숫자보다 이 그래프가 정직합니다.
          </p>
          <div className="card" style={{ height: 300 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="predicted"
                  type="number"
                  domain={[0, 1]}
                  stroke="var(--muted)"
                  fontSize={11}
                  label={{
                    value: "예측 확률",
                    position: "insideBottom",
                    offset: -4,
                    fill: "var(--muted)",
                    fontSize: 11,
                  }}
                />
                <YAxis
                  domain={[0, 1]}
                  stroke="var(--muted)"
                  fontSize={11}
                  label={{
                    value: "실제 빈도",
                    angle: -90,
                    position: "insideLeft",
                    fill: "var(--muted)",
                    fontSize: 11,
                  }}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  formatter={(v: number, name: string) => [
                    v.toFixed(3),
                    name === "observed" ? "실제 빈도" : "완벽한 보정",
                  ]}
                />
                <Line
                  type="monotone"
                  dataKey="ideal"
                  stroke="var(--muted)"
                  strokeDasharray="4 4"
                  dot={false}
                  name="ideal"
                />
                <Line
                  type="monotone"
                  dataKey="observed"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  name="observed"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}

      <div className="caveat">{q.literature_context}</div>
    </>
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
  tone?: "good" | "bad";
}) {
  return (
    <div className="card metric">
      <div className="label">{label}</div>
      <div
        className="value"
        style={{
          color:
            tone === "bad"
              ? "var(--bad)"
              : tone === "good"
                ? "var(--good)"
                : undefined,
        }}
      >
        {value}
      </div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}
