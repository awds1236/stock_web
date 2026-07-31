"use client";

import { api, pct, type SectorRow } from "@/lib/api";
import { groupLabel } from "@/lib/sectorNames";
import { useEffect, useState } from "react";

export default function SectorsPage() {
  const [market, setMarket] = useState("US");
  const [level, setLevel] = useState<"sector" | "industry">("industry");
  const [rows, setRows] = useState<SectorRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows([]);
    api
      .sectors(market, level)
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((e) => {
        setRows([]);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [market, level]);

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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

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

const cls = (v: number | null) =>
  v === null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
