"use client";

import { ForecastQualityPanel } from "@/components/ForecastQualityPanel";
import { api, IS_STATIC, STATIC_HORIZON, type Forecast } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

const TARGETS = [
  {
    id: "direction",
    label: "상승 확률",
    hint: "보정된 확률로 제시합니다. 점 예측이 아닙니다.",
  },
  {
    id: "volatility",
    label: "변동성",
    hint: "문헌상 수익률 방향보다 훨씬 예측 가능한 대상입니다.",
  },
  {
    id: "return",
    label: "기대 수익률",
    hint: "가장 어려운 대상입니다. 문헌 최고 수준이 월간 R² 0.4% 입니다.",
  },
];

export default function ForecastPage() {
  const [market, setMarket] = useState("US");
  const [target, setTarget] = useState("direction");
  const [horizon, setHorizon] = useState(21);
  const [data, setData] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      setData(await api.forecast(market, target, horizon));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [market, target, horizon]);

  useEffect(() => {
    run();
  }, [run]);

  const t = TARGETS.find((x) => x.id === target)!;

  return (
    <>
      <h2>예측</h2>
      <p className="sub">
        워크포워드로 재학습하며, 표시되는 예측은 전부 아웃오브샘플입니다. 예측값은
        그 예측의 품질과 <strong>항상 함께</strong> 표시됩니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {TARGETS.map((x) => (
              <option key={x.id} value={x.id}>
                {x.label}
              </option>
            ))}
          </select>
          <select
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
          >
            {/* 정적 스냅샷에는 21일 예측만 포함됩니다. 선택해도 실패할 옵션을
                보여주는 대신 처음부터 빼둡니다. */}
            {!IS_STATIC && <option value={5}>5일</option>}
            <option value={STATIC_HORIZON}>21일 (1개월)</option>
            {!IS_STATIC && <option value={63}>63일 (3개월)</option>}
          </select>
          <button onClick={run} disabled={loading}>
            {loading ? "계산 중…" : "다시 계산"}
          </button>
        </div>
        <div className="caveat">{t.hint}</div>
      </div>

      {error && (
        <div className="banner warn">
          <strong>예측을 실행할 수 없습니다</strong>
          {error}
        </div>
      )}

      {loading && (
        <div className="card muted">
          워크포워드 재학습 중입니다. 데이터 양에 따라 수십 초 걸릴 수 있습니다.
        </div>
      )}

      {data && (
        <>
          <ForecastQualityPanel q={data.quality} target={data.target} />

          <h3>
            최신 예측 상위 종목 ({data.horizon_days}일 기준)
          </h3>
          <p className="sub">
            아래 순위는 위의 품질 지표를 전제로만 의미가 있습니다. skill score 가
            0 이하라면 이 순위는 사실상 무작위입니다.
          </p>
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>종목</th>
                  <th className="num">
                    {target === "direction"
                      ? "상승 확률"
                      : target === "volatility"
                        ? "예상 변동성"
                        : "기대 수익률"}
                  </th>
                  <th className="num">기준일</th>
                </tr>
              </thead>
              <tbody>
                {data.latest.map((r, i) => (
                  <tr key={r.ticker}>
                    <td className="muted">{i + 1}</td>
                    <td>{r.ticker}</td>
                    <td className="num">
                      {target === "return"
                        ? `${(r.prediction * 100).toFixed(2)}%`
                        : target === "direction"
                          ? `${(r.prediction * 100).toFixed(1)}%`
                          : r.prediction.toFixed(4)}
                    </td>
                    <td className="num muted">{r.date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="banner info">
            <strong>이 시장의 데이터 한계</strong>
            {data.caveat}
          </div>
        </>
      )}
    </>
  );
}
