"use client";

import { StockSearch } from "@/components/StockSearch";
import { api, type StockDetail, type UniverseItem } from "@/lib/api";
import { industryLabel, sectorLabel } from "@/lib/sectorNames";
import { useCallback, useEffect, useState } from "react";
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
        setTicker(u.length ? u[0].ticker : null);
        setError(u.length ? null : "이 시장에 수집된 데이터가 없습니다.");
      })
      .catch((e) => {
        setTicker(null);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [market]);

  const load = useCallback(async () => {
    if (!ticker) return;
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

  const chart =
    detail?.prices.map((p, i) => ({
      date: p.date.slice(0, 10),
      close: p.close,
      sma20: detail.indicators.values.sma_20?.[i] ?? null,
      sma60: detail.indicators.values.sma_60?.[i] ?? null,
    })) ?? [];

  const rsiChart =
    detail?.indicators.date.map((d, i) => ({
      date: d.slice(0, 10),
      rsi: detail.indicators.values.rsi_14?.[i] ?? null,
    })) ?? [];

  return (
    <>
      <h2>종목 분석</h2>
      <p className="sub">
        지표는 <strong>상태 서술</strong>이지 매매 신호가 아닙니다. 각 지표에
        해석의 한계를 함께 표시합니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <StockSearch
            market={market}
            selected={ticker}
            onSelect={setTicker}
          />
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

      {detail && (
        <>
          <h3>
            {detail.ticker}
            {detail.industry ? (
              <span className="muted"> · {industryLabel(detail.industry)}</span>
            ) : detail.sector ? (
              <span className="muted"> · {sectorLabel(detail.sector)}</span>
            ) : null}
          </h3>

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
                    stroke={
                      lv.kind === "support" ? "var(--good)" : "var(--bad)"
                    }
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
  );
}
