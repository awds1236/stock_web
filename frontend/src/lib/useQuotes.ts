"use client";

import { api, type Quote } from "@/lib/api";
import {
  browserQuotesSupported,
  fetchBrowserQuotes,
  KR_BROWSER_NOTE,
} from "@/lib/liveQuote";
import { useBackend, usePolling } from "@/lib/useBackend";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/** 기본 갱신 주기. 원천이 지연 시세라 더 자주 물어도 같은 값이 옵니다. */
export const DEFAULT_QUOTE_INTERVAL_MS = 60_000;

export type QuoteOrigin =
  | "backend-live" // 백엔드가 시세 소스에서 받아온 값
  | "backend-stored" // 백엔드가 가진 마지막 확정 종가
  | "browser" // 브라우저가 직접 시세 소스에서 받아온 값
  | "snapshot"; // CI 가 구운 스냅샷의 종가 (움직이지 않음)

export type ResolvedQuote = {
  ticker: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  currency: string | null;
  origin: QuoteOrigin;
  note: string;
  at: number;
};

export type QuotesState = {
  quotes: Map<string, ResolvedQuote>;
  updatedAt: number | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
};

/**
 * 종목들의 현재가 -- **가장 살아있는 경로**를 자동으로 고릅니다.
 *
 *   백엔드 연결됨 -> 서버가 조회 (가장 안정적, 실패가 서버 로그에 남음)
 *   아니면 미국   -> 브라우저가 직접 시세 소스 호출
 *   아니면        -> 넘겨받은 스냅샷 종가 (움직이지 않음을 화면에 표시)
 *
 * `fallbackCloses` 를 받는 이유: 시세 조회가 실패해도 표의 가격 칸이 비지
 * 않게 하기 위해서입니다. 대신 `origin` 이 항상 어느 경로였는지 알려주므로,
 * 화면은 "지금 값"과 "구운 값"을 절대 섞어 보여주지 않습니다.
 */
export function useQuotes(
  market: string,
  tickers: string[],
  options: {
    fallbackCloses?: Map<string, number | null>;
    intervalMs?: number;
    enabled?: boolean;
  } = {},
): QuotesState {
  const {
    fallbackCloses,
    intervalMs = DEFAULT_QUOTE_INTERVAL_MS,
    enabled = true,
  } = options;
  const backend = useBackend();

  const [quotes, setQuotes] = useState<Map<string, ResolvedQuote>>(new Map());
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 목록 자체가 매 렌더 새 배열이면 이펙트가 무한히 다시 돕니다.
  const key = tickers.join(",");
  const list = useMemo(() => (key ? key.split(",") : []), [key]);

  const fallbackRef = useRef(fallbackCloses);
  fallbackRef.current = fallbackCloses;

  const load = useCallback(async () => {
    if (!enabled || list.length === 0) return;
    setLoading(true);
    try {
      const resolved = await resolveQuotes(
        market,
        list,
        backend.mode === "live",
        fallbackRef.current,
      );
      setQuotes(resolved.quotes);
      setError(resolved.error);
      setUpdatedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [market, list, backend.mode, enabled]);

  useEffect(() => {
    load();
  }, [load]);

  usePolling(load, enabled && list.length ? intervalMs : 0, [load]);

  return { quotes, updatedAt, error, loading, refresh: load };
}

async function resolveQuotes(
  market: string,
  tickers: string[],
  live: boolean,
  fallbackCloses: Map<string, number | null> | undefined,
): Promise<{ quotes: Map<string, ResolvedQuote>; error: string | null }> {
  const out = new Map<string, ResolvedQuote>();
  let error: string | null = null;

  if (live) {
    try {
      for (const q of await api.quotes(market, tickers)) out.set(q.ticker, fromApi(q));
      return { quotes: out, error: null };
    } catch (e) {
      // 백엔드가 붙어 있는데 실패했다면 브라우저 직접 호출로 내려가지 않습니다.
      // 두 경로가 섞이면 같은 화면의 가격이 서로 다른 출처가 됩니다.
      error = e instanceof Error ? e.message : String(e);
      return { quotes: withFallback(out, tickers, fallbackCloses, error), error };
    }
  }

  if (browserQuotesSupported(market)) {
    try {
      const browser = await fetchBrowserQuotes(market, tickers);
      for (const [ticker, q] of browser) {
        const change =
          q.previousClose !== null && q.previousClose !== 0
            ? q.price - q.previousClose
            : null;
        out.set(ticker, {
          ticker,
          price: q.price,
          previousClose: q.previousClose,
          change,
          changePct:
            q.previousClose !== null && q.previousClose !== 0
              ? q.price / q.previousClose - 1
              : null,
          currency: q.currency,
          origin: "browser",
          note:
            `${q.source} 를 브라우저에서 직접 조회했습니다. 무료 소스라 통상 ` +
            "15분 이상 지연되며, 체결 가격이 아닙니다.",
          at: q.at,
        });
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  } else {
    error = KR_BROWSER_NOTE;
  }

  return { quotes: withFallback(out, tickers, fallbackCloses, error), error };
}

/** 조회하지 못한 종목은 스냅샷 종가로 채우되 **출처를 바꿔 표시**합니다. */
function withFallback(
  out: Map<string, ResolvedQuote>,
  tickers: string[],
  fallbackCloses: Map<string, number | null> | undefined,
  note: string | null,
): Map<string, ResolvedQuote> {
  if (!fallbackCloses) return out;
  for (const ticker of tickers) {
    if (out.has(ticker)) continue;
    const close = fallbackCloses.get(ticker);
    if (close === undefined || close === null) continue;
    out.set(ticker, {
      ticker,
      price: close,
      previousClose: null,
      change: null,
      changePct: null,
      currency: null,
      origin: "snapshot",
      note:
        "스냅샷에 저장된 종가입니다 -- 지금 값이 아닙니다." +
        (note ? ` (현재가 조회 실패: ${note})` : ""),
      at: 0,
    });
  }
  return out;
}

function fromApi(q: Quote): ResolvedQuote {
  return {
    ticker: q.ticker,
    price: q.price,
    previousClose: q.previous_close,
    change: q.change,
    changePct: q.change_pct,
    currency: q.currency,
    origin: q.source === "live" ? "backend-live" : "backend-stored",
    note: q.note,
    at: Date.now(),
  };
}

export const ORIGIN_LABEL: Record<QuoteOrigin, string> = {
  "backend-live": "지연 시세",
  "backend-stored": "저장된 종가",
  browser: "지연 시세",
  snapshot: "스냅샷 종가",
};

/** 이 값이 시간에 따라 움직이는가. 화면이 "멈춤" 배지를 붙일지 결정합니다. */
export function isMoving(origin: QuoteOrigin): boolean {
  return origin === "backend-live" || origin === "browser";
}
