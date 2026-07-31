"use client";

import { api, IS_STATIC, pct, type Quote } from "@/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

const POLL_MS = 60_000;

/**
 * 현재가 표시 + 주기적 갱신.
 *
 * **실시간이 아니라는 것을 화면에서 숨기지 않습니다.** 무료 소스는 지연
 * 시세이고 한국은 장중 소스가 아예 없습니다. 숫자만 크게 띄우면 사용자는
 * 체결 가격으로 읽습니다 -- 그래서 `source` 배지와 백엔드가 준 설명을 항상
 * 함께 표시합니다.
 *
 * 폴링 주기가 1분인 이유: 원천이 15분 지연이므로 더 자주 물어도 같은 값이
 * 돌아옵니다. 무료 소스에 초 단위로 요청하면 차단만 앞당깁니다.
 */
export function LiveQuote({ market, ticker }: { market: string; ticker: string }) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      setQuote(await api.quote(market, ticker));
      setUpdatedAt(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [market, ticker]);

  useEffect(() => {
    if (IS_STATIC) return;
    load();
    timer.current = setInterval(load, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [load]);

  if (IS_STATIC) return null;
  if (error)
    return (
      <div className="card">
        <span className="muted">현재가를 가져오지 못했습니다. {error}</span>
      </div>
    );
  if (!quote) return null;

  const tone = quote.change === null ? "" : quote.change > 0 ? "pos" : quote.change < 0 ? "neg" : "";

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 14 }}>
          <span style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
            {quote.price === null
              ? "—"
              : quote.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          <span className={tone} style={{ fontSize: 14 }}>
            {quote.change === null
              ? ""
              : `${quote.change > 0 ? "+" : ""}${quote.change.toLocaleString(undefined, {
                  maximumFractionDigits: 2,
                })} (${pct(quote.change_pct)})`}
          </span>
          <span className="muted" style={{ fontSize: 11 }}>
            {quote.currency ?? ""}
          </span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <span
            className={quote.source === "live" ? "muted" : "muted"}
            style={{
              fontSize: 11,
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "2px 6px",
            }}
          >
            {quote.source === "live"
              ? "지연 시세"
              : `저장된 종가${quote.as_of ? ` (${quote.as_of})` : ""}`}
          </span>
          {updatedAt && (
            <span className="muted" style={{ fontSize: 11 }}>
              {updatedAt.toLocaleTimeString()} 갱신
            </span>
          )}
        </div>
      </div>
      <div className="caveat">{quote.note}</div>
    </div>
  );
}
