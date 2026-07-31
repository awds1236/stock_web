"use client";

import { api, pct, type Watchlist } from "@/lib/api";
import { industryLabel, sectorLabel } from "@/lib/sectorNames";
import { useEffect, useState } from "react";

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

  useEffect(() => {
    setLoading(true);
    setData(null);
    setError(null);
    api
      .watchlist(market)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
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
        {data && (
          <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>
            기준일 {data.as_of}
          </span>
        )}
      </div>

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
                          {c.close === null ? "—" : fmtPrice(c.close)}
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
