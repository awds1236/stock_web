"use client";

import { AIAnalysisCard } from "@/components/AIAnalysisCard";
import { Freshness } from "@/components/Freshness";
import { api, pct, type SectorReport, type SectorRow } from "@/lib/api";
import { groupLabel } from "@/lib/sectorNames";
import { usePolling } from "@/lib/useBackend";
import { useCallback, useEffect, useState } from "react";

/** 업종 집계는 일별 데이터에서 나옵니다. 백엔드 수집이 돌면 알아채도록만
    주기적으로 다시 읽습니다. */
const SECTOR_POLL_MS = 300_000;

export default function SectorsPage() {
  const [market, setMarket] = useState("US");
  const [level, setLevel] = useState<"sector" | "industry">("industry");
  const [rows, setRows] = useState<SectorRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  // 업종 상세는 **고른 업종 하나만** 계산합니다. 표의 모든 업종에 대해 구성종목
  // 분해까지 미리 돌리면 화면이 뜨는 데만 수십 초가 걸립니다.
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SectorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const loadRows = useCallback(
    async (clear = true) => {
      setLoading(true);
      if (clear) {
        setRows([]);
        setSelected(null);
        setDetail(null);
      }
      try {
        setRows(await api.sectors(market, level));
        setUpdatedAt(Date.now());
        setError(null);
      } catch (e) {
        setRows([]);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [market, level],
  );

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  usePolling(() => loadRows(false), SECTOR_POLL_MS, [loadRows]);

  const analyze = useCallback(
    async (sector: string) => {
      setSelected(sector);
      setDetail(null);
      setDetailError(null);
      setBusy(true);
      try {
        const r = await api.analyzeSector(market, sector, level);
        setDetail(r.report);
      } catch (e) {
        setDetailError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [market, level],
  );

  return (
    <>
      <h2>섹터 분석</h2>
      <p className="sub">
        문헌상 섹터·국가 단위 아웃오브샘플 R²(0.29~0.95%)는 개별종목(0.33~0.40%)과
        동등하거나 더 높습니다. &quot;어떤 종목이 오를까&quot;보다{" "}
        <strong>&quot;어떤 업종이 오를까&quot;</strong>가 근거상 더 다룰 만한
        질문입니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as "sector" | "industry")}
          >
            <option value="industry">세부업종 (반도체·은행 등)</option>
            <option value="sector">대분류 (11개 섹터)</option>
          </select>
        </div>
        <div className="caveat">
          대분류만 보면 반도체와 소프트웨어가 모두 &ldquo;기술&rdquo;로 뭉개집니다.
          기본값을 세부업종으로 둔 이유입니다.
        </div>
      </div>

      <Freshness
        updatedAt={updatedAt}
        onRefresh={() => loadRows(false)}
        loading={loading}
        intervalMs={SECTOR_POLL_MS}
      />

      {error && (
        <div className="banner warn">
          <strong>표시할 수 없습니다</strong>
          {error}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="card">
            <table>
              <thead>
                <tr>
                  <th>{level === "industry" ? "세부업종" : "섹터"}</th>
                  <th className="num">20일 수익률</th>
                  <th className="num">60일 수익률</th>
                  <th className="num">상대강도(60일)</th>
                  <th className="num">상승 비율</th>
                  <th className="num">종목 수</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.sector}>
                    <td>{groupLabel(r.sector, level)}</td>
                    <td className={`num ${cls(r.ret_20d)}`}>{pct(r.ret_20d)}</td>
                    <td className={`num ${cls(r.ret_60d)}`}>{pct(r.ret_60d)}</td>
                    <td className={`num ${cls(r.relative_strength_60d)}`}>
                      {pct(r.relative_strength_60d)}
                    </td>
                    <td className="num">{pct(r.breadth, 0)}</td>
                    <td className="num muted">{r.n_constituents}</td>
                    <td className="num">
                      <button
                        className="ghost"
                        disabled={busy}
                        onClick={() => analyze(r.sector)}
                      >
                        {busy && selected === r.sector ? "분석 중…" : "분석"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {detailError && (
            <div className="banner warn">
              <strong>업종 분석 실패</strong>
              {detailError}
            </div>
          )}

          {detail && (
            <>
              <h3>{groupLabel(detail.sector, level)} 상세</h3>
              <div className="grid grid-4">
                <div className="card metric">
                  <div className="label">20일 수익률</div>
                  <div className={`value ${cls(detail.ret_20d)}`}>
                    {pct(detail.ret_20d)}
                  </div>
                  <div className="note">
                    시장 {pct(detail.market_ret_20d)} · {detail.rank_by_ret_20d ?? "—"}위 /{" "}
                    {detail.n_sectors}
                  </div>
                </div>
                <div className="card metric">
                  <div className="label">상대강도 60일</div>
                  <div className={`value ${cls(detail.relative_strength_60d)}`}>
                    {pct(detail.relative_strength_60d)}
                  </div>
                  <div className="note">시장 평균 대비 초과수익 누적</div>
                </div>
                <div className="card metric">
                  <div className="label">상승 비율</div>
                  <div className="value">{pct(detail.breadth, 0)}</div>
                  <div className="note">
                    {detail.breadth !== null && detail.breadth < 0.5
                      ? "절반 미만 — 소수 종목이 끌었습니다"
                      : "업종 전반"}
                  </div>
                </div>
                <div className="card metric">
                  <div className="label">구성 종목</div>
                  <div className="value">{detail.n_constituents}</div>
                  <div className="note">수집된 유니버스 기준</div>
                </div>
              </div>

              <div className="grid grid-2">
                <div className="card">
                  <strong>업종 내 상위</strong>
                  <MemberTable rows={detail.leaders} />
                </div>
                <div className="card">
                  <strong>업종 내 하위</strong>
                  {detail.laggards.length ? (
                    <MemberTable rows={detail.laggards} />
                  ) : (
                    <div className="caveat" style={{ marginTop: 8 }}>
                      구성 종목이 {detail.n_constituents}개뿐이라 전부 위 표에
                      표시됩니다. 같은 종목을 상위와 하위 양쪽에 넣으면 업종 내
                      분화가 있는 것처럼 보입니다.
                    </div>
                  )}
                </div>
              </div>

              {/* 국면 · 집중도 · 선행후행. 업종 수익률만 보면 "왜 지금 이런가"와
                  "이 강세가 얼마나 넓은가"를 알 수 없습니다. */}
              <div className="grid grid-2">
                <div className="card">
                  <strong>업종 국면</strong>
                  <div style={{ marginTop: 6 }}>
                    {detail.regime?.summary ?? "판정 불가"}
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {detail.regime?.trend?.basis}
                  </div>
                  {detail.concentration?.available && (
                    <div style={{ marginTop: 8, fontSize: 13 }}>
                      상위 {detail.concentration.top_k}종목을 빼면 20일 수익률이{" "}
                      <strong className={cls(detail.concentration.ex_top_ret_20d)}>
                        {pct(detail.concentration.ex_top_ret_20d)}
                      </strong>{" "}
                      <span className="muted">
                        (전체 {pct(detail.concentration.mean_ret_20d)})
                      </span>
                    </div>
                  )}
                  {detail.regime?.caveat && (
                    <div className="caveat">{detail.regime.caveat}</div>
                  )}
                </div>

                <div className="card">
                  <strong>선행 · 후행 관계</strong>
                  {detail.lead_lag?.available ? (
                    <>
                      {detail.lead_lag.led_by.length === 0 &&
                      detail.lead_lag.leads.length === 0 ? (
                        <div className="muted" style={{ marginTop: 6 }}>
                          이 업종과 얽힌 상위 쌍이 없습니다.
                        </div>
                      ) : (
                        <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 13 }}>
                          {detail.lead_lag.led_by.map((p) => (
                            <li key={`in-${p.leader}`}>
                              <strong>{p.leader}</strong> 가 이 업종을 선행 (r=
                              {p.corr.toFixed(2)}){" "}
                              <span className={p.significant ? "pos" : "muted"}>
                                {p.significant ? "보정 후 유의" : "문턱 미달"}
                              </span>
                            </li>
                          ))}
                          {detail.lead_lag.leads.map((p) => (
                            <li key={`out-${p.follower}`}>
                              이 업종이 <strong>{p.follower}</strong> 를 선행 (r=
                              {p.corr.toFixed(2)}){" "}
                              <span className={p.significant ? "pos" : "muted"}>
                                {p.significant ? "보정 후 유의" : "문턱 미달"}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                      <div className="caveat">{detail.lead_lag.note}</div>
                    </>
                  ) : (
                    <div className="muted" style={{ marginTop: 6 }}>
                      {detail.lead_lag?.reason ?? "선행-후행을 계산할 수 없습니다."}
                    </div>
                  )}
                </div>
              </div>

              <div className="card">
                {detail.caveats.map((c) => (
                  <div className="caveat" key={c}>
                    {c}
                  </div>
                ))}
              </div>

              <h3>AI 서술 분석</h3>
              <AIAnalysisCard
                title={`${groupLabel(detail.sector, level)} — AI 업종 분석`}
                subject="이 업종"
                run={() => api.aiAnalyzeSector(market, detail.sector, level)}
                historyQuery={{ kind: "sector", market, subject: detail.sector }}
              />
            </>
          )}

          <div className="card">
            <strong>읽는 법</strong>
            <div className="caveat" style={{ marginTop: 8 }}>
              <strong>상대강도</strong>는 시장 평균 대비 초과수익 누적입니다.
              절대 수익률로 섹터를 고르면 시장이 전체적으로 오른 구간에서 모든
              섹터가 좋아 보입니다.
            </div>
            <div className="caveat">
              <strong>상승 비율(breadth)</strong>이 낮은데 수익률이 높다면, 소수
              종목이 그 업종을 끌어올렸다는 뜻입니다. 업종 전반의 강세와 몇 종목의
              강세는 다른 사건입니다.
            </div>
            <div className="caveat">
              반론도 함께 기억하십시오 — 불확실성을 제대로 반영하지 않으면
              횡단면 섹터 예측력의 증거는 거의 남지 않는다는 연구가 있습니다.
            </div>
          </div>
        </>
      )}
    </>
  );
}

function MemberTable({
  rows,
}: {
  rows: { ticker: string; name: string | null; ret_20d: number | null }[];
}) {
  if (!rows.length) return <div className="muted">표시할 종목이 없습니다.</div>;
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
