"use client";

import { AIAnalysisCard } from "@/components/AIAnalysisCard";
import { api, pct, type MarketReport } from "@/lib/api";
import { groupLabel } from "@/lib/sectorNames";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

export default function MarketPage() {
  const [market, setMarket] = useState("US");
  const [report, setReport] = useState<MarketReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const r = await api.analyzeMarket(market);
      setReport(r.report);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [market]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h2>시장 분석</h2>
      <p className="sub">
        시장 전체 상태와 <strong>규칙에 걸린 주목 종목</strong>. 여기서
        &quot;시장&quot;은 공식 지수가 아니라 수집된 시총 상위 종목의 동일가중
        평균입니다 — 지수와 수치가 다른 것이 정상입니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <button onClick={load} disabled={busy}>
            {busy ? "계산 중…" : "다시 계산"}
          </button>
          {report && <span className="muted">기준일 {report.as_of}</span>}
        </div>
      </div>

      {error && (
        <div className="banner warn">
          <strong>표시할 수 없습니다</strong>
          {error}
        </div>
      )}

      {report && (
        <>
          <h3>시장 상태</h3>
          <div className="grid grid-4">
            <Metric
              label="20일 (동일가중)"
              value={pct(report.index_proxy.ret_20d)}
              tone={cls(report.index_proxy.ret_20d)}
              note={`60일 ${pct(report.index_proxy.ret_60d)}`}
            />
            <Metric
              label="60일선 위 종목"
              value={pct(report.internals.above_sma60_pct, 0)}
              note={`${report.internals.n_evaluated}종목 평가`}
            />
            <Metric
              label="52주 고가 근접"
              value={pct(report.internals.near_52w_high_pct, 0)}
              note="고점 대비 -5% 이내"
            />
            <Metric
              label="변동성 중앙값"
              value={pct(report.internals.median_vol_20d, 0)}
              note="20일 실현변동성 (연율)"
            />
          </div>
          <div className="card">
            <div className="caveat">
              평균 수익률과 내부지표(60일선 위 비율)가 어긋난다면, 지수는 올랐지만
              대부분의 종목은 그렇지 않다는 뜻입니다. 평균만 보면 이 차이가
              보이지 않습니다.
            </div>
          </div>

          <h3>업종 지형</h3>
          <div className="grid grid-2">
            <div className="card">
              <strong>상위 업종</strong>
              <SectorTable rows={report.sectors_top} level={report.level} />
            </div>
            <div className="card">
              <strong>하위 업종</strong>
              {report.sectors_bottom.length ? (
                <SectorTable rows={report.sectors_bottom} level={report.level} />
              ) : (
                <div className="caveat" style={{ marginTop: 8 }}>
                  업종 수가 적어 전부 위 표에 표시됩니다.
                </div>
              )}
            </div>
          </div>

          <h3>주목 종목 ({report.attention.length})</h3>
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>종목</th>
                  <th className="num">20일</th>
                  <th className="num">52주 고점 대비</th>
                  <th className="num">규칙</th>
                  <th>걸린 이유</th>
                </tr>
              </thead>
              <tbody>
                {report.attention.map((a) => (
                  <tr key={a.ticker}>
                    <td>
                      <Link href={`/stocks?ticker=${a.ticker}`}>
                        {a.name ?? a.ticker}
                      </Link>
                      <span className="muted"> · {a.ticker}</span>
                    </td>
                    <td className={`num ${cls(a.ret_20d)}`}>{pct(a.ret_20d)}</td>
                    <td className="num">{pct(a.pct_from_52w_high)}</td>
                    <td className="num">{a.score}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {a.reasons.join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {report.caveats.map((c) => (
              <div className="caveat" key={c}>
                {c}
              </div>
            ))}
          </div>

          <h3>움직인 종목</h3>
          <div className="grid grid-2">
            <div className="card">
              <strong>20일 상승 상위</strong>
              <MoverTable rows={report.movers_up} />
            </div>
            <div className="card">
              <strong>20일 하락 상위</strong>
              {report.movers_down.length ? (
                <MoverTable rows={report.movers_down} />
              ) : (
                <div className="caveat" style={{ marginTop: 8 }}>
                  수집된 종목이 적어 전부 위 표에 표시됩니다.
                </div>
              )}
            </div>
          </div>

          <h3>AI 서술 분석</h3>
          <AIAnalysisCard
            title={`${market === "KR" ? "한국" : "미국"} 시장 — AI 종합 분석`}
            subject="이 시장"
            run={() => api.aiAnalyzeMarket(market)}
            historyQuery={{ kind: "market", market, subject: market }}
          />
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

function SectorTable({
  rows,
  level,
}: {
  rows: { sector: string; ret_20d: number | null; breadth: number | null }[];
  level: string;
}) {
  if (!rows.length) return <div className="muted">업종 정보가 없습니다.</div>;
  return (
    <table style={{ marginTop: 8 }}>
      <tbody>
        {rows.map((r) => (
          <tr key={r.sector}>
            <td>{groupLabel(r.sector, level as "sector" | "industry")}</td>
            <td className={`num ${cls(r.ret_20d)}`}>{pct(r.ret_20d)}</td>
            <td className="num muted">상승 {pct(r.breadth, 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MoverTable({
  rows,
}: {
  rows: { ticker: string; name: string | null; ret_20d: number | null }[];
}) {
  return (
    <table style={{ marginTop: 8 }}>
      <tbody>
        {rows.map((r) => (
          <tr key={r.ticker}>
            <td>
              {r.name ?? r.ticker}
              <span className="muted"> · {r.ticker}</span>
            </td>
            <td className={`num ${cls(r.ret_20d)}`}>{pct(r.ret_20d)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const cls = (v: number | null | undefined) =>
  v === null || v === undefined ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
