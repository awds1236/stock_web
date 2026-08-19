"use client";

import { Freshness, QuoteCell } from "@/components/Freshness";
import { api, pct, type Watchlist } from "@/lib/api";
import { industryLabel, sectorLabel } from "@/lib/sectorNames";
import { usePolling } from "@/lib/useBackend";
import { useQuotes } from "@/lib/useQuotes";
import { useCallback, useEffect, useMemo, useState } from "react";

/** 규칙 판정은 일별 데이터에서 나오므로 자주 다시 계산할 이유가 없습니다.
    그래도 두는 이유: 백엔드에서 수집이 돌면 열어둔 탭이 알아채야 합니다. */
const WATCH_POLL_MS = 300_000;

/**
 * 자동 관찰 목록.
 *
 * "추천"이라는 말을 쓰지 않습니다. 규칙에 걸린 후보를 규칙과 함께 보여줄 뿐이며,
 * 규칙 목록과 한계를 화면에서 접을 수 없게 상단에 고정합니다 -- 근거를 숨긴
 * 종목 리스트는 이 앱의 원칙(검증 없는 신호 금지)과 정면으로 충돌합니다.
 */
export default function WatchlistPage() {
  const [market, setMarket] = useState("US");
  const [data, setData] = useState<Watchlist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  const load = useCallback(
    async (clear = true) => {
      setLoading(true);
      if (clear) setData(null);
      setError(null);
      try {
        setData(await api.watchlist(market));
        setUpdatedAt(Date.now());
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [market],
  );

  useEffect(() => {
    load();
  }, [load]);

  // 자동 갱신은 화면을 비우지 않습니다 -- 읽는 도중에 목록이 사라지면 안 됩니다.
  usePolling(() => load(false), WATCH_POLL_MS, [load]);

  // 현재가는 **고른 종목 하나만** 조회합니다.
  //
  // 예전에는 목록에 있는 후보 12개를 전부 조회했습니다. 그러면 이 화면을
  // 열어두는 것만으로 1분마다 12번의 요청이 나가고, 무료 시세 소스는 그
  // 빈도에 차단으로 응답합니다. 목록 자체는 스냅샷 종가로 이미 완전히
  // 읽을 수 있으므로, 지금 값이 필요한 종목만 사용자가 고르게 합니다.
  const [selected, setSelected] = useState<string | null>(null);
  const picked = useMemo(
    () => (data?.candidates ?? []).find((c) => c.ticker === selected) ?? null,
    [data, selected],
  );
  const requests = useMemo(
    () => (picked ? [{ ticker: picked.ticker, board: picked.board ?? null }] : []),
    [picked],
  );
  const fallbackCloses = useMemo(
    () => new Map((data?.candidates ?? []).map((c) => [c.ticker, c.close])),
    [data],
  );
  const quotes = useQuotes(market, requests, { fallbackCloses });

  // 시장을 바꾸면 이전 시장의 선택이 남으면 안 됩니다 -- 코드가 우연히 겹치면
  // 다른 종목의 가격을 그 종목의 것으로 표시하게 됩니다.
  useEffect(() => {
    setSelected(null);
  }, [market]);

  return (
    <>
      <h2>관찰 목록</h2>
      <p className="sub">
        검색하지 않아도 규칙에 걸린 업종과 종목을 자동으로 모아 보여줍니다.
        <strong> 매수 추천이 아닙니다</strong> — 어떤 규칙에 걸렸는지를 함께
        표시하니 그 근거를 보고 판단하십시오.
      </p>

      <div className="card">
        <select value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="US">미국</option>
          <option value="KR">한국</option>
        </select>
      </div>

      <Freshness
        updatedAt={updatedAt}
        onRefresh={() => load(false)}
        loading={loading}
        intervalMs={WATCH_POLL_MS}
        asOf={data?.as_of}
        extra={
          <span>
            · 현재가는 <strong>고른 한 종목만</strong> 1분마다 갱신
          </span>
        }
      />

      {error && (
        <div className="banner warn">
          <strong>표시할 수 없습니다</strong>
          {error}
        </div>
      )}
      {loading && <div className="card muted">계산 중…</div>}

      {data && (
        <>
          <div className="banner warn">
            <strong>이 목록을 읽는 법</strong>
            {data.caveat}
          </div>

          <h3>적용된 규칙</h3>
          <div className="card">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {data.rules.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <div className="caveat" style={{ marginTop: 8 }}>
              score = 걸린 규칙 개수입니다. 가중치나 예측 확률이 아닙니다.
            </div>
          </div>

          <h3>상승 추세 업종</h3>
          {data.rising_sectors.length === 0 ? (
            <div className="card muted">
              현재 조건(상대강도 &gt; 0, 상승 비율 ≥ 50%)을 만족하는 업종이
              없습니다. 시장 전반이 약세이거나 상승이 소수 종목에 편중된
              상태입니다.
            </div>
          ) : (
            <div className="card">
              <table>
                <thead>
                  <tr>
                    <th>업종</th>
                    <th className="num">20일 수익률</th>
                    <th className="num">상대강도(60일)</th>
                    <th className="num">상승 비율</th>
                    <th className="num">종목 수</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rising_sectors.map((s) => (
                    <tr key={s.sector}>
                      <td>{industryLabel(s.sector)}</td>
                      <td className={`num ${cls(s.ret_20d)}`}>
                        {pct(s.ret_20d)}
                      </td>
                      <td className={`num ${cls(s.relative_strength_60d)}`}>
                        {pct(s.relative_strength_60d)}
                      </td>
                      <td className="num">{pct(s.breadth, 0)}</td>
                      <td className="num muted">{s.n_constituents}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="caveat" style={{ marginTop: 8 }}>
                상승 비율이 50% 미만인 업종은 제외했습니다 — 소수 종목이 끌어올린
                업종을 &ldquo;상승 추세&rdquo;로 부르면 오해를 부릅니다.
              </div>
            </div>
          )}

          <h3>주목할 종목 후보</h3>
          {data.candidates.length === 0 ? (
            <div className="card muted">
              규칙에 걸린 종목이 없습니다. 조건을 만족하는 종목이 없는 것도
              정보입니다.
            </div>
          ) : (
            <div className="grid grid-2">
              {data.candidates.map((c) => (
                <div className="card" key={c.ticker}>
                  <div
                    className="row"
                    style={{ justifyContent: "space-between" }}
                  >
                    <strong>
                      {c.ticker}
                      {c.name ? (
                        <span className="muted" style={{ fontWeight: 400 }}>
                          {" "}
                          {c.name}
                        </span>
                      ) : null}
                    </strong>
                    <span className="pos">규칙 {c.score}개</span>
                  </div>

                  <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                    {c.industry
                      ? industryLabel(c.industry)
                      : sectorLabel(c.sector)}
                  </div>

                  <table style={{ marginTop: 8 }}>
                    <tbody>
                      <tr>
                        <td className="muted">현재가</td>
                        <td className="num">
                          <QuoteCell
                            quote={
                              selected === c.ticker
                                ? quotes.quotes.get(c.ticker)
                                : undefined
                            }
                            fallback={c.close}
                          />
                          {selected === c.ticker ? (
                            <span
                              className="muted"
                              style={{ marginLeft: 6, fontSize: 11 }}
                            >
                              {quotes.loading ? "조회 중…" : "1분마다 갱신"}
                            </span>
                          ) : (
                            <button
                              className="ghost tiny"
                              style={{ marginLeft: 6 }}
                              onClick={() => setSelected(c.ticker)}
                              title="이 종목만 현재가를 조회합니다 (1분마다 갱신)"
                            >
                              현재가 조회
                            </button>
                          )}
                        </td>
                      </tr>
                      <tr>
                        <td className="muted">20일 수익률</td>
                        <td className={`num ${cls(c.ret_20d)}`}>
                          {pct(c.ret_20d)}
                        </td>
                      </tr>
                      <tr>
                        <td className="muted">52주 고점 대비</td>
                        <td className={`num ${cls(c.pct_from_52w_high)}`}>
                          {pct(c.pct_from_52w_high)}
                        </td>
                      </tr>
                    </tbody>
                  </table>

                  <div style={{ marginTop: 8 }}>
                    {c.reasons.map((r) => (
                      <div key={r} className="caveat" style={{ marginTop: 2 }}>
                        ✓ {r}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

const cls = (v: number | null) =>
  v === null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";

/** 가격 표기: 1000 이상은 정수, 그 미만은 소수 둘째 자리까지.
 *  원화(수천~수만)와 달러(수십~수백)를 같은 규칙으로 읽히게 합니다. */
function fmtPrice(v: number): string {
  return v >= 1000
    ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
