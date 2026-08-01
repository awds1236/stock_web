/**
 * 현재가 조회 -- **브라우저에서 직접**.
 *
 * 왜 필요한가:
 *   GitHub Pages 는 정적 파일만 서빙합니다. 빌드 때 구운 값만 보여주면 배포한
 *   순간에 시간이 멈춥니다. 백엔드 없이도 가격만큼은 지금 값이 되게 하려면,
 *   브라우저가 시세 소스를 직접 호출하는 수밖에 없습니다.
 *
 * 이 경로의 제약 -- 숨기지 않고 화면에 그대로 표시합니다:
 *
 *   1. **실시간이 아닙니다.** 무료 소스는 통상 15분 이상 지연됩니다.
 *      1분마다 새로 물어도 원천이 지연 시세라는 사실은 변하지 않습니다.
 *   2. **차단될 수 있습니다.** 브라우저에서 직접 호출하려면 소스가 CORS 를
 *      허용해야 하는데, 이들은 비공식 엔드포인트라 언제든 막힐 수 있습니다.
 *      막히면 조용히 실패하는 대신 이유를 표시하고 스냅샷 종가로 되돌립니다.
 *   3. **한국 종목은 대상이 아닙니다.** 무료로 쓸 수 있는 장중 소스가 없습니다.
 *
 * 백엔드가 연결되어 있으면 이 경로를 쓰지 않습니다 -- 서버가 조회하는 편이
 * 안정적이고, 실패 원인도 서버 로그에 남기 때문입니다.
 */

export type BrowserQuote = {
  ticker: string;
  price: number;
  previousClose: number | null;
  currency: string | null;
  source: string; // 어느 소스가 답했는지. 화면에 표시합니다
  at: number;
};

export class QuoteUnavailable extends Error {}

/** 한 소스가 실패해도 다음을 시도합니다. 하나에 묶으면 그 하나가 막힐 때 끝입니다. */
type Source = {
  name: string;
  fetchQuotes: (tickers: string[]) => Promise<Map<string, BrowserQuote>>;
};

const YAHOO_HOSTS = [
  "https://query1.finance.yahoo.com",
  "https://query2.finance.yahoo.com",
];

/**
 * Yahoo Finance 차트 엔드포인트.
 *
 * 공식 API 가 아닙니다. 규격이 예고 없이 바뀔 수 있고 CORS 허용 여부도
 * 보장되지 않습니다. 그래서 실패를 정상 경로로 취급합니다.
 */
const yahooChart: Source = {
  name: "Yahoo Finance (지연 시세)",
  async fetchQuotes(tickers) {
    const out = new Map<string, BrowserQuote>();
    // 종목마다 한 번씩 부릅니다. 일괄 엔드포인트(/v7/finance/quote)는 인증
    // 쿠키를 요구하도록 바뀌어 브라우저에서 쓸 수 없습니다.
    const results = await Promise.allSettled(
      tickers.map((t) => fetchYahooOne(t)),
    );
    for (const r of results) {
      if (r.status === "fulfilled" && r.value) out.set(r.value.ticker, r.value);
    }
    if (out.size === 0) throw new QuoteUnavailable("Yahoo 응답에서 가격을 얻지 못했습니다.");
    return out;
  },
};

async function fetchYahooOne(ticker: string): Promise<BrowserQuote | null> {
  let lastError: unknown = null;
  for (const host of YAHOO_HOSTS) {
    try {
      const url =
        `${host}/v8/finance/chart/${encodeURIComponent(ticker)}` +
        `?interval=1m&range=1d`;
      const res = await withTimeout(fetch(url, { cache: "no-store" }), 8000);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      const result = body?.chart?.result?.[0];
      const meta = result?.meta;
      if (!meta) throw new Error("응답에 meta 가 없습니다");

      const price = numberOrNull(meta.regularMarketPrice) ?? lastClose(result);
      if (price === null) throw new Error("응답에 가격이 없습니다");

      return {
        ticker,
        price,
        previousClose:
          numberOrNull(meta.chartPreviousClose) ??
          numberOrNull(meta.previousClose),
        currency: typeof meta.currency === "string" ? meta.currency : null,
        source: yahooChart.name,
        at: Date.now(),
      };
    } catch (e) {
      lastError = e;
    }
  }
  if (lastError) throw lastError;
  return null;
}

/** 장중 마지막 유효 체결가. meta 에 현재가가 없을 때의 대비책입니다. */
function lastClose(result: unknown): number | null {
  const closes = (result as { indicators?: { quote?: { close?: unknown }[] } })
    ?.indicators?.quote?.[0]?.close;
  if (!Array.isArray(closes)) return null;
  for (let i = closes.length - 1; i >= 0; i--) {
    const v = numberOrNull(closes[i]);
    if (v !== null) return v;
  }
  return null;
}

// 순서가 곧 우선순위입니다.
const SOURCES: Source[] = [yahooChart];

/**
 * 브라우저가 한 번에 조회할 종목 수 상한.
 *
 * 종목마다 요청이 하나입니다. 표에 30종목이 있으면 1분마다 30번의 요청이
 * 한 브라우저에서 나가고, 그건 무료 소스가 차단으로 응답하기 딱 좋은 양입니다.
 * 백엔드 경로에는 서버 쪽 상한이 따로 있습니다.
 */
export const MAX_BROWSER_QUOTES = 12;

/** 이 시장을 브라우저에서 직접 조회할 수 있는가. */
export function browserQuotesSupported(market: string): boolean {
  return market.toUpperCase() === "US";
}

export const KR_BROWSER_NOTE =
  "한국은 브라우저에서 직접 부를 수 있는 무료 장중 시세 소스가 없습니다. " +
  "백엔드를 연결하면 저장된 확정 데이터를, 연결하지 않으면 스냅샷 종가를 " +
  "표시합니다.";

/**
 * 여러 종목의 현재가.
 *
 * 실패한 종목은 결과에서 빠집니다 -- 하나가 실패했다고 표 전체의 가격을
 * 지우면, 화면이 고장난 것처럼 보입니다.
 */
export async function fetchBrowserQuotes(
  market: string,
  tickers: string[],
): Promise<Map<string, BrowserQuote>> {
  if (!browserQuotesSupported(market)) throw new QuoteUnavailable(KR_BROWSER_NOTE);
  if (tickers.length === 0) return new Map();

  const capped = tickers.slice(0, MAX_BROWSER_QUOTES);
  const errors: string[] = [];
  for (const source of SOURCES) {
    try {
      return await source.fetchQuotes(capped);
    } catch (e) {
      errors.push(`${source.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  throw new QuoteUnavailable(
    "브라우저에서 시세 소스에 접근하지 못했습니다. 무료 소스는 비공식이라 " +
      "차단되거나 규격이 바뀔 수 있습니다. 백엔드를 연결하면 서버가 대신 " +
      `조회합니다. (시도: ${errors.join(" / ")})`,
  );
}

function numberOrNull(v: unknown): number | null {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${ms}ms 안에 응답 없음`)), ms),
    ),
  ]);
}
