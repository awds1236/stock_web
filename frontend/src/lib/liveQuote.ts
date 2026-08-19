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
 *
 * 한국이 여기 포함된 경위:
 *   예전에는 이 파일이 미국만 다뤘고, 한국은 "무료 장중 소스가 없다"며
 *   조회를 아예 시도하지 않았습니다. 그래서 한국 종목 화면은 배포 시점의
 *   종가에 멈춰 있었습니다 -- 그것이 '당일 가격을 가져올 수 없다'의 원인입니다.
 *
 *   KRX Open API 에 장중 시세가 없다는 것과, 어떤 무료 소스에도 없다는 것은
 *   다른 이야기입니다. 종목코드에 보드별 접미사를 붙이면(.KS / .KQ) 미국과
 *   같은 소스가 한국 종목의 지연 시세를 줍니다.
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

/** 조회 대상 한 건. 보드는 한국 종목의 심볼 접미사를 고르는 데 씁니다. */
export type QuoteRequest = {
  ticker: string;
  board?: string | null; // "KOSPI" | "KOSDAQ" | null
};

export class QuoteUnavailable extends Error {}

/** 한 소스가 실패해도 다음을 시도합니다. 하나에 묶으면 그 하나가 막힐 때 끝입니다. */
type Source = {
  name: string;
  fetchQuotes: (
    market: string,
    requests: QuoteRequest[],
  ) => Promise<Map<string, BrowserQuote>>;
};

const YAHOO_HOSTS = [
  "https://query1.finance.yahoo.com",
  "https://query2.finance.yahoo.com",
];

/**
 * 종목코드 -> 시세 소스의 심볼 (앞이 우선).
 *
 * 한국 종목코드는 그 자체로는 소스가 알아듣지 못합니다. 보드별 접미사가
 * 필요하고(.KS = 유가증권, .KQ = 코스닥), 보드를 모르면 둘 다 시도합니다.
 * 백엔드에도 같은 규칙이 있습니다(providers/quote.py `quote_symbols`).
 */
export function quoteSymbols(market: string, req: QuoteRequest): string[] {
  const ticker = req.ticker.trim();
  if (market.toUpperCase() !== "KR") return [ticker];
  const board = (req.board ?? "").trim().toUpperCase();
  if (board === "KOSPI") return [`${ticker}.KS`];
  if (board === "KOSDAQ") return [`${ticker}.KQ`];
  return [`${ticker}.KS`, `${ticker}.KQ`];
}

/**
 * Yahoo Finance 차트 엔드포인트.
 *
 * 공식 API 가 아닙니다. 규격이 예고 없이 바뀔 수 있고 CORS 허용 여부도
 * 보장되지 않습니다. 그래서 실패를 정상 경로로 취급합니다.
 */
const yahooChart: Source = {
  name: "Yahoo Finance (지연 시세)",
  async fetchQuotes(market, requests) {
    const out = new Map<string, BrowserQuote>();
    // 종목마다 한 번씩 부릅니다. 일괄 엔드포인트(/v7/finance/quote)는 인증
    // 쿠키를 요구하도록 바뀌어 브라우저에서 쓸 수 없습니다.
    const results = await Promise.allSettled(
      requests.map((r) => fetchYahooOne(market, r)),
    );
    for (const r of results) {
      if (r.status === "fulfilled" && r.value) out.set(r.value.ticker, r.value);
    }
    if (out.size === 0) throw new QuoteUnavailable("Yahoo 응답에서 가격을 얻지 못했습니다.");
    return out;
  },
};

async function fetchYahooOne(
  market: string,
  req: QuoteRequest,
): Promise<BrowserQuote | null> {
  let lastError: unknown = null;
  // 심볼 후보 x 호스트. 보드를 아는 종목은 후보가 하나뿐이라 요청도 하나입니다.
  for (const symbol of quoteSymbols(market, req)) {
    for (const host of YAHOO_HOSTS) {
      try {
        const url =
          `${host}/v8/finance/chart/${encodeURIComponent(symbol)}` +
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
          ticker: req.ticker,
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
  }
  if (lastError) throw lastError;
  return null;
}

/**
 * 브라우저가 한 번에 조회할 종목 수 상한.
 *
 * 종목마다 요청이 하나입니다. 표에 30종목이 있으면 1분마다 30번의 요청이
 * 한 브라우저에서 나가고, 그건 무료 소스가 차단으로 응답하기 딱 좋은 양입니다.
 *
 * 화면은 **선택한 한 종목만** 조회하므로 평소에는 1입니다. 이 상한은 실수로
 * 목록 전체를 넘겼을 때의 방어선입니다.
 */
export const MAX_BROWSER_QUOTES = 5;

/**
 * 같은 종목을 다시 물을 수 있는 최소 간격.
 *
 * 무료 소스가 차단으로 응답하게 만드는 것은 대개 빈도입니다. 화면이 몇 개든,
 * 컴포넌트가 몇 번 다시 마운트되든, 사용자가 새로고침을 몇 번 누르든 이 값이
 * 실제 네트워크 호출의 상한을 정합니다. 갱신 주기 설정과 **별개로** 강제되는
 * 하한이라는 점이 중요합니다 -- 주기 설정만 두면 다른 경로(수동 새로고침,
 * 탭 복귀, 재마운트)로 얼마든지 새어 나갑니다.
 *
 * 어차피 원천이 지연 시세라 더 자주 물어도 같은 값이 옵니다.
 */
export const MIN_QUOTE_INTERVAL_MS = 60_000;

type CacheEntry = { at: number; quote: BrowserQuote };
const cache = new Map<string, CacheEntry>();

/** 캐시 키. 시장이 다르면 같은 코드라도 다른 종목입니다. */
function cacheKey(market: string, req: QuoteRequest): string {
  return `${market.toUpperCase()}/${req.ticker}/${req.board ?? ""}`;
}

/** 이 종목을 지금 다시 물을 수 있는가. 아니면 몇 ms 를 더 기다려야 하는가. */
export function msUntilRefetch(market: string, req: QuoteRequest): number {
  const hit = cache.get(cacheKey(market, req));
  if (!hit) return 0;
  return Math.max(0, MIN_QUOTE_INTERVAL_MS - (Date.now() - hit.at));
}

/** 테스트·시장 전환용. 운영 화면에서 부를 일은 없습니다. */
export function clearQuoteCache(): void {
  cache.clear();
}

/** 이 시장을 브라우저에서 직접 조회할 수 있는가. */
export function browserQuotesSupported(market: string): boolean {
  const m = market.toUpperCase();
  return m === "US" || m === "KR";
}

export const KR_DELAY_NOTE =
  "한국 지연 시세입니다(통상 15~20분 지연). KRX 공식 실시간이 아니며 체결 " +
  "가격도 아닙니다 -- 참고용입니다.";

// 순서가 곧 우선순위입니다.
const SOURCES: Source[] = [yahooChart];

/**
 * 여러 종목의 현재가.
 *
 * 실패한 종목은 결과에서 빠집니다 -- 하나가 실패했다고 표 전체의 가격을
 * 지우면, 화면이 고장난 것처럼 보입니다.
 *
 * 60초 안에 이미 받은 종목은 네트워크로 나가지 않고 직전 응답을 돌려줍니다.
 */
export async function fetchBrowserQuotes(
  market: string,
  requests: QuoteRequest[],
): Promise<Map<string, BrowserQuote>> {
  if (!browserQuotesSupported(market))
    throw new QuoteUnavailable(
      `${market} 는 브라우저에서 직접 조회할 수 있는 시세 소스가 없습니다.`,
    );
  if (requests.length === 0) return new Map();

  const capped = requests.slice(0, MAX_BROWSER_QUOTES);
  const out = new Map<string, BrowserQuote>();
  const stale: QuoteRequest[] = [];
  for (const req of capped) {
    const hit = cache.get(cacheKey(market, req));
    if (hit && Date.now() - hit.at < MIN_QUOTE_INTERVAL_MS) {
      out.set(req.ticker, hit.quote);
    } else {
      stale.push(req);
    }
  }
  // 전부 캐시에서 나왔으면 호출할 것이 없습니다. 이때 소스 실패로 처리하면
  // 방금 받은 값이 있는데도 화면이 오류를 띄웁니다.
  if (stale.length === 0) return out;

  const errors: string[] = [];
  for (const source of SOURCES) {
    try {
      const fresh = await source.fetchQuotes(market, stale);
      for (const [ticker, quote] of fresh) {
        out.set(ticker, quote);
        const req = stale.find((r) => r.ticker === ticker);
        if (req) cache.set(cacheKey(market, req), { at: Date.now(), quote });
      }
      return out;
    } catch (e) {
      errors.push(`${source.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  // 일부라도 캐시에서 나왔으면 그것은 살립니다. 소스가 막혔다고 이미 가진
  // 값까지 버릴 이유는 없습니다.
  if (out.size > 0) return out;
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

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${ms}ms 안에 응답 없음`)), ms),
    ),
  ]);
}
