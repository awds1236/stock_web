"use client";

import { AIAnalysisCard } from "@/components/AIAnalysisCard";
import { LiveQuote } from "@/components/LiveQuote";
import { StockSearch } from "@/components/StockSearch";
import {
  api,
  num,
  pct,
  type StockAnalysis,
  type StockDetail,
  type UniverseItem,
} from "@/lib/api";
import { industryLabel, sectorLabel } from "@/lib/sectorNames";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export default function StocksPage() {
  const [market, setMarket] = useState("US");
  const [universe, setUniverse] = useState<UniverseItem[]>([]);
  const [ticker, setTicker] = useState<string | null>(null);
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 분석은 **선택한 종목에 대해서만**, 버튼을 눌렀을 때 실행합니다.
  const [analysis, setAnalysis] = useState<StockAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [withForecast, setWithForecast] = useState(false);

  // 시장 화면의 "주목 종목"에서 넘어올 때 그 종목을 바로 엽니다.
  // useSearchParams 대신 마운트 시점에 한 번 읽는 이유: 정적 내보내기
  // (output: 'export')에서 useSearchParams 는 Suspense 경계를 요구하고,
  // 여기서 필요한 건 최초 1회 값뿐입니다.
  const [wanted, setWanted] = useState<string | null>(null);
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("ticker");
    if (t) setWanted(t);
  }, []);

  useEffect(() => {
    // 시장을 바꾸면 이전 시장의 상세를 즉시 지웁니다. 지우지 않으면 "데이터
    // 없음" 배너 아래에 이전 시장의 차트가 남아, 없는 데이터가 있는 것처럼
    // 보입니다 (실배포에서 확인된 버그).
    setDetail(null);
    setUniverse([]);
    api
      .universe(market)
      .then((u) => {
        setUniverse(u);
        const preferred = u.find((x) => x.ticker === wanted)?.ticker;
        setTicker(preferred ?? (u.length ? u[0].ticker : null));
        setError(u.length ? null : "이 시장에 수집된 데이터가 없습니다.");
      })
      .catch((e) => {
        setTicker(null);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [market, wanted]);

  // 종목을 고르면 차트·지표는 **자동으로** 불러옵니다. 이건 저장된 데이터를
  // 읽는 값싼 작업이라 버튼 뒤에 둘 이유가 없습니다. 버튼 뒤에 두는 것은
  // 유니버스 전체를 훑는 분석과 유료인 AI 호출뿐입니다.
  const load = useCallback(async () => {
    if (!ticker) return;
    setAnalysis(null);
    setAnalysisError(null);
    try {
      setDetail(await api.stock(market, ticker));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [market, ticker]);

  useEffect(() => {
    load();
  }, [load]);

  const runAnalysis = useCallback(async () => {
    if (!ticker) return;
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      setAnalysis(await api.analyzeStock(market, ticker, withForecast));
    } catch (e) {
      setAnalysisError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  }, [market, ticker, withForecast]);

  const chart = useMemo(
    () =>
      detail?.prices.map((p, i) => ({
        date: p.date.slice(0, 10),
        close: p.close,
        sma20: detail.indicators.values.sma_20?.[i] ?? null,
        sma60: detail.indicators.values.sma_60?.[i] ?? null,
      })) ?? [],
    [detail],
  );

  const rsiChart = useMemo(
    () =>
      detail?.indicators.date.map((d, i) => ({
        date: d.slice(0, 10),
        rsi: detail.indicators.values.rsi_14?.[i] ?? null,
      })) ?? [],
    [detail],
  );

  const report = analysis?.report;

  return (
    <>
      <h2>종목 분석</h2>
      <p className="sub">
        차트와 지표는 종목을 고르면 <strong>자동으로</strong> 불러옵니다. 유니버스
        전체를 훑는 상대분석과 AI 서술은 <strong>버튼을 누른 종목에 대해서만</strong>{" "}
        실행됩니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <StockSearch market={market} selected={ticker} onSelect={setTicker} />
        </div>
        {universe.length > 0 && (
          <div className="caveat">
            수집된 {universe.length.toLocaleString()}종목 중에서 검색합니다.
            코드·이름·업종 중 무엇이든 일부만 입력하면 됩니다.
          </div>
        )}
      </div>

      {error && (
        <div className="banner warn">
          <strong>표시할 수 없습니다</strong>
          {error}
        </div>
      )}

      {detail && ticker && (
        <>
          <h3>
            {detail.name ?? detail.ticker}
            <span className="muted"> · {detail.ticker}</span>
            {detail.industry ? (
              <span className="muted"> · {industryLabel(detail.industry)}</span>
            ) : detail.sector ? (
              <span className="muted"> · {sectorLabel(detail.sector)}</span>
            ) : null}
          </h3>

          <LiveQuote market={market} ticker={ticker} />

          <div className="card" style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--muted)" fontSize={11} minTickGap={40} />
                <YAxis stroke="var(--muted)" fontSize={11} domain={["auto", "auto"]} />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="close" name="종가" stroke="var(--accent)" dot={false} strokeWidth={2} />
                <Line dataKey="sma20" name="20일선" stroke="var(--good)" dot={false} strokeWidth={1} />
                <Line dataKey="sma60" name="60일선" stroke="var(--warn)" dot={false} strokeWidth={1} />
                {/* 지지/저항 참조선. 신호가 아니라 참고선이므로 점선 + 흐린 색. */}
                {detail.levels.map((lv) => (
                  <ReferenceLine
                    key={`${lv.kind}-${lv.price}`}
                    y={lv.price}
                    stroke={lv.kind === "support" ? "var(--good)" : "var(--bad)"}
                    strokeDasharray="5 4"
                    strokeOpacity={0.55}
                    label={{
                      value: `${lv.kind === "support" ? "지지" : "저항"} ${lv.price.toLocaleString(
                        undefined,
                        { maximumFractionDigits: 0 },
                      )} (${lv.touches}회)`,
                      position: "insideTopLeft",
                      fill: "var(--muted)",
                      fontSize: 10,
                    }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card" style={{ height: 180 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rsiChart}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--muted)" fontSize={11} minTickGap={40} />
                <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={11} />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line dataKey="rsi" name="RSI(14)" stroke="var(--accent)" dot={false} strokeWidth={1.5} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <h3>이 종목 분석하기</h3>
          <div className="card">
            <div className="row">
              <button onClick={runAnalysis} disabled={analyzing}>
                {analyzing ? "분석 중…" : `${detail.name ?? ticker} 분석 실행`}
              </button>
              <label className="row" style={{ gap: 6, fontSize: 12 }}>
                <input
                  type="checkbox"
                  checked={withForecast}
                  onChange={(e) => setWithForecast(e.target.checked)}
                  style={{ width: "auto" }}
                />
                워크포워드 예측 포함
              </label>
            </div>
            <div className="caveat">
              예측은 횡단면 모형이라 유니버스 전체를 학습해야 계산됩니다. 같은
              데이터에 대해서는 한 번만 계산하고 재사용하지만, 첫 실행은 수십 초가
              걸립니다. 지표·상대강도 분석만 필요하면 체크를 끄십시오.
            </div>
          </div>

          {analysisError && (
            <div className="banner warn">
              <strong>분석 실패</strong>
              {analysisError}
            </div>
          )}

          {report && (
            <>
              <div className="grid grid-4">
                <Metric label="추세" value={report.trend.stack.split(" ")[0]}
                        note={report.trend.stack} />
                <Metric
                  label="20일 수익률"
                  value={pct(report.returns["20d"])}
                  note={`유니버스 대비 ${pct(report.relative.excess_vs_universe_20d)}`}
                  tone={signTone(report.returns["20d"])}
                />
                <Metric
                  label="60일 순위"
                  value={
                    report.relative.rank_60d
                      ? `${report.relative.rank_60d.rank} / ${report.relative.rank_60d.of}`
                      : "—"
                  }
                  note="수집된 유니버스 안에서의 순위"
                />
                <Metric
                  label="걸린 규칙"
                  value={`${report.rules.score} / ${report.rules.all.length}`}
                  note="점수가 아니라 개수입니다"
                />
              </div>

              <h3>규칙 판정</h3>
              <div className="card">
                {report.rules.matched.length ? (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {report.rules.matched.map((r) => (
                      <li key={r} className="pos">
                        {r}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="muted">걸린 규칙이 없습니다.</div>
                )}
                <div className="caveat">{report.rules.caveat}</div>
              </div>

              <h3>수치 요약</h3>
              <div className="card">
                <table>
                  <tbody>
                    <Row label="종가" value={fmt(report.price.close)} />
                    <Row label="52주 고점 대비"
                         value={pct(report.indicators.pct_from_52w_high)} />
                    <Row label="RSI(14)" value={num(report.indicators.rsi_14, 1)} />
                    <Row label="실현변동성 20일 (연율)"
                         value={pct(report.indicators.vol_20d)} />
                    <Row label="20/60일선 교차"
                         value={crossText(report.trend.cross_20_60)} />
                    <Row label="50/200일선 교차"
                         value={crossText(report.trend.cross_50_200)} />
                    <Row
                      label="거래대금 5일/60일"
                      value={
                        report.liquidity.surge_ratio
                          ? `${report.liquidity.surge_ratio.toFixed(2)}배`
                          : "—"
                      }
                    />
                    <Row
                      label={`업종(${report.relative.sector ?? "—"}) 20일`}
                      value={pct(report.relative.sector_ret_20d)}
                    />
                    <Row label="업종 대비 초과"
                         value={pct(report.relative.excess_vs_sector_20d)} />
                    <Row
                      label="데이터"
                      value={`${report.data_quality.n_days}일 (${report.data_quality.first_date} ~ ${report.data_quality.last_date})`}
                    />
                  </tbody>
                </table>
              </div>

              {analysis?.forecast_error && (
                <div className="banner warn">
                  <strong>예측을 계산하지 못했습니다</strong>
                  {analysis.forecast_error}
                </div>
              )}

              {analysis?.forecast && analysis.forecast.latest.length > 0 && (
                <>
                  <h3>이 종목의 예측</h3>
                  <div className="card">
                    <div>
                      {analysis.forecast.horizon_days}일 뒤 상승 확률 추정:{" "}
                      <strong>
                        {pct(analysis.forecast.latest[0].prediction, 1)}
                      </strong>{" "}
                      <span className="muted">
                        ({analysis.forecast.latest[0].date} 기준)
                      </span>
                    </div>
                    <div className="caveat">
                      이 확률의 skill score 는{" "}
                      {num(analysis.forecast.quality.skill_score, 4)} 입니다. 0 이하면
                      기저율(그냥 &quot;오른다&quot;고 답하기)보다 나을 것이 없다는
                      뜻이며, 그런 확률은 매매 근거가 되지 못합니다. 자세한 신뢰도
                      곡선은 예측 화면에 있습니다.
                    </div>
                  </div>
                </>
              )}

              <h3>지표 해석</h3>
              <div className="grid grid-2">
                {report.interpretation.map((i) => (
                  <div className="card" key={i.indicator}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <strong>{i.indicator}</strong>
                      <span className="muted">{i.state}</span>
                    </div>
                    <div style={{ marginTop: 6 }}>{i.reading}</div>
                    <div className="caveat">{i.caveat}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          <h3>AI 서술 분석</h3>
          <AIAnalysisCard
            title={`${detail.name ?? ticker} — AI 종목 분석`}
            subject="이 종목"
            run={() => api.aiAnalyzeStock(market, ticker)}
            historyQuery={{ kind: "stock", market, subject: ticker }}
          />

          {!report && (
            <>
              <h3>지표 해석</h3>
              <div className="grid grid-2">
                {detail.interpretation.map((i) => (
                  <div className="card" key={i.indicator}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <strong>{i.indicator}</strong>
                      <span className="muted">{i.state}</span>
                    </div>
                    <div style={{ marginTop: 6 }}>{i.reading}</div>
                    <div className="caveat">{i.caveat}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
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

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td className="muted">{label}</td>
      <td className="num">{value}</td>
    </tr>
  );
}

function crossText(c: { state: string; last_cross: string | null; days_since_cross: number | null }) {
  if (c.state === "insufficient") return "데이터 부족";
  const now = c.state === "golden" ? "정배열" : "역배열";
  if (!c.last_cross || c.days_since_cross === null) return `${now} (구간 내 교차 없음)`;
  const kind = c.last_cross === "golden" ? "골든크로스" : "데드크로스";
  return `${now} · ${c.days_since_cross}일 전 ${kind}`;
}

function fmt(v: number | null | undefined) {
  return v === null || v === undefined
    ? "—"
    : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function signTone(v: number | null | undefined) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}
