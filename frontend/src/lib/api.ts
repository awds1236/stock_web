/**
 * 백엔드 API 클라이언트.
 *
 * 설계 규칙: 예측(`Forecast`)에서 `quality` 는 **선택 필드가 아닙니다.**
 * 예측 확률만 떼어내 화면에 띄우는 것이 타입 수준에서 불가능해야 합니다.
 * 보정되지 않은 확률은 과신을 낳고, 과신하는 예측은 통상적 베팅 규칙 하에서
 * 장기 성장률을 음수로 만듭니다.
 */

export type Coverage = {
  market: string;
  n_tickers: number;
  first_date: string | null;
  last_date: string | null;
  n_rows: number;
  ready: boolean;
  needs_credential: string | null;
};

export type UniverseItem = {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  first_date: string;
  last_date: string;
  n_days: number;
};

export type PricePoint = {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
};

export type Interpretation = {
  indicator: string;
  value: number;
  state: string;
  reading: string;
  caveat: string;
};

export type Level = {
  price: number;
  kind: "support" | "resistance";
  touches: number;
  distance_pct: number;
};

export type StockDetail = {
  market: string;
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  prices: PricePoint[];
  indicators: { date: string[]; values: Record<string, (number | null)[]> };
  interpretation: Interpretation[];
  levels: Level[];
};

export type WatchSector = {
  sector: string;
  ret_20d: number | null;
  relative_strength_60d: number | null;
  breadth: number | null;
  n_constituents: number;
};

export type WatchCandidate = {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  close: number | null;
  ret_20d: number | null;
  pct_from_52w_high: number | null;
  score: number;
  reasons: string[];
};

export type SearchHit = {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  match: "ticker" | "name" | "industry";
};

export type Watchlist = {
  market: string;
  as_of: string;
  rising_sectors: WatchSector[];
  candidates: WatchCandidate[];
  rules: string[];
  caveat: string;
};

export type ForecastQuality = {
  n_folds: number;
  n_predictions: number;
  oos_r2: number | null; // 기준선 0 대비. 시장 드리프트 포함
  oos_r2_cross: number | null; // 횡단면 평균 대비. 종목 선별력만
  mean_daily_ic: number | null; // 일별 순위상관 평균. 드리프트 무관
  brier: number | null;
  reliability: number | null;
  resolution: number | null;
  skill_score: number | null;
  ece: number | null;
  reliability_curve: {
    bin_lower: number;
    bin_upper: number;
    n: number;
    predicted: number | null;
    observed: number | null;
  }[];
  baseline_label: string | null;
  baseline_r2: number | null;
  beats_baseline: boolean | null;
  leakage_warning: string | null;
  literature_context: string;
};

export type Forecast = {
  market: string;
  target: string;
  horizon_days: number;
  latest: { date: string; ticker: string; prediction: number }[];
  quality: ForecastQuality; // 필수
  caveat: string;
};

export type SectorRow = {
  sector: string;
  ret_20d: number | null;
  ret_60d: number | null;
  relative_strength_60d: number | null;
  breadth: number | null;
  n_constituents: number;
};

export type Credential = {
  name: string;
  label: string;
  help: string;
  signup_url: string;
  configured: boolean;
  source: "env" | "stored" | "none";
  masked: string;
  editable: boolean;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/**
 * 정적 모드 (GitHub Pages).
 *
 * Pages 는 정적 파일만 서빙하므로 백엔드가 없습니다. 빌드 시
 * NEXT_PUBLIC_STATIC=1 이면 /api/* 대신 CI 가 생성해 둔 JSON 스냅샷
 * (/data/*.json)을 읽습니다. 쓰기 동작(수집·인증정보)은 정적 배포에서
 * 불가능하며, 되는 척하는 대신 명확한 메시지로 실패합니다.
 */
export const IS_STATIC = process.env.NEXT_PUBLIC_STATIC === "1";
const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
export const STATIC_HORIZON = 21;

const STATIC_WRITE_MSG =
  "정적 배포(GitHub Pages)에서는 이 동작을 실행할 수 없습니다. 데이터는 " +
  "GitHub Actions 가 스케줄에 따라 자동 갱신하며, 설정·수집은 로컬 실행" +
  "(백엔드 포함)에서만 가능합니다.";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      // FastAPI 는 오류를 detail 에 담습니다. 이 문구가 사용자에게 다음 행동을
      // 알려주므로 그대로 전달합니다 ("먼저 수집하십시오" 등).
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* 본문이 JSON 이 아니면 상태 코드만 씁니다 */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json();
}

async function staticFile<T>(name: string): Promise<T> {
  const res = await fetch(`${BASE}/data/${name}`, { cache: "no-store" });
  if (!res.ok) {
    throw new ApiError(
      res.status === 404
        ? "이 정적 스냅샷에는 해당 데이터가 포함되어 있지 않습니다. " +
          "(예: 한국 데이터는 저장소에 KRX_AUTH_KEY secret 이 설정된 경우에만 " +
          "포함됩니다)"
        : `정적 데이터 로드 실패: HTTP ${res.status}`,
      res.status,
    );
  }
  return res.json();
}

function staticWriteBlocked(): never {
  throw new ApiError(STATIC_WRITE_MSG, 501);
}

export const api = {
  coverage: () =>
    IS_STATIC
      ? staticFile<Coverage[]>("coverage.json")
      : req<Coverage[]>("/api/coverage"),
  universe: (market: string) =>
    IS_STATIC
      ? staticFile<UniverseItem[]>(`universe-${market}.json`)
      : req<UniverseItem[]>(`/api/universe/${market}`),
  stock: (market: string, ticker: string) =>
    IS_STATIC
      ? staticFile<StockDetail>(`stocks-${market}-${ticker}.json`)
      : req<StockDetail>(`/api/stocks/${market}/${ticker}`),
  forecast: (market: string, target: string, horizon: number) => {
    if (!IS_STATIC)
      return req<Forecast>(
        `/api/forecast/${market}?target=${target}&horizon_days=${horizon}`,
      );
    // 스냅샷에는 21일 예측만 포함됩니다. 없는 조합을 404 로 흘리는 대신
    // 이유를 말합니다.
    if (horizon !== STATIC_HORIZON)
      throw new ApiError(
        `정적 스냅샷에는 ${STATIC_HORIZON}일 예측만 포함됩니다. 다른 기간은 ` +
          "로컬 실행에서 계산할 수 있습니다.",
        404,
      );
    return staticFile<Forecast>(
      `forecast-${market}-${target}-${STATIC_HORIZON}.json`,
    );
  },
  sectors: (market: string, level: "sector" | "industry" = "sector") =>
    IS_STATIC
      ? staticFile<SectorRow[]>(
          level === "industry"
            ? `sectors-${market}-industry.json`
            : `sectors-${market}.json`,
        )
      : req<SectorRow[]>(`/api/sectors/${market}?level=${level}`),
  watchlist: (market: string) =>
    IS_STATIC
      ? staticFile<Watchlist>(`watchlist-${market}.json`)
      : req<Watchlist>(`/api/watchlist/${market}`),
  search: async (market: string, q: string, limit = 20) =>
    IS_STATIC
      ? searchLocally(await api.universe(market), q, limit)
      : req<SearchHit[]>(
          `/api/search/${market}?q=${encodeURIComponent(q)}&limit=${limit}`,
        ),
  buildInfo: () =>
    staticFile<{ generated_at: string; note: string }>("build-info.json"),
  credentials: () =>
    IS_STATIC
      ? staticWriteBlocked()
      : req<{ credentials: Credential[]; warning: string }>(
          "/api/settings/credentials",
        ),
  setCredential: (name: string, value: string) =>
    IS_STATIC
      ? staticWriteBlocked()
      : req<Credential>(`/api/settings/credentials/${name}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        }),
  deleteCredential: (name: string) =>
    IS_STATIC
      ? staticWriteBlocked()
      : req<Credential>(`/api/settings/credentials/${name}`, {
          method: "DELETE",
        }),
  ingest: (market: string, years = 10) =>
    IS_STATIC
      ? staticWriteBlocked()
      : req<{
          market: string;
          rows: number;
          tickers: number;
          start: string | null;
          end: string | null;
          warnings: string[];
        }>(`/api/ingest/${market}?years=${years}`, { method: "POST" }),
};

/**
 * 정적 배포용 클라이언트 검색.
 *
 * Pages 에는 백엔드가 없으므로 universe 스냅샷을 받아 브라우저에서 거릅니다.
 * **백엔드와 같은 순위 규칙**을 씁니다 -- 규칙이 갈리면 로컬과 배포에서 검색
 * 결과가 달라지고, 그건 추적하기 어려운 종류의 버그입니다.
 *
 * 시총 정렬은 스냅샷에 시총이 없어 불가능하므로, 동점 시 종목코드 순입니다.
 */
function searchLocally(
  universe: UniverseItem[],
  q: string,
  limit: number,
): SearchHit[] {
  const toHit = (u: UniverseItem, match: SearchHit["match"]): SearchHit => ({
    ticker: u.ticker,
    name: u.name,
    sector: u.sector,
    industry: u.industry,
    market_cap: null,
    match,
  });

  const needle = q.trim().toLowerCase();
  if (!needle) return universe.slice(0, limit).map((u) => toHit(u, "ticker"));

  const scored: { rank: number; hit: SearchHit }[] = [];
  for (const u of universe) {
    const ticker = u.ticker.toLowerCase();
    const name = (u.name ?? "").toLowerCase();
    const industry = (u.industry ?? "").toLowerCase();
    const sector = (u.sector ?? "").toLowerCase();

    if (ticker === needle) scored.push({ rank: 0, hit: toHit(u, "ticker") });
    else if (ticker.startsWith(needle))
      scored.push({ rank: 1, hit: toHit(u, "ticker") });
    else if (name.startsWith(needle))
      scored.push({ rank: 2, hit: toHit(u, "name") });
    else if (name.includes(needle))
      scored.push({ rank: 3, hit: toHit(u, "name") });
    else if (industry.includes(needle) || sector.includes(needle))
      scored.push({ rank: 4, hit: toHit(u, "industry") });
  }

  scored.sort(
    (a, b) => a.rank - b.rank || a.hit.ticker.localeCompare(b.hit.ticker),
  );
  return scored.slice(0, limit).map((s) => s.hit);
}

export const pct = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !isFinite(v)
    ? "—"
    : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 4) =>
  v === null || v === undefined || !isFinite(v) ? "—" : v.toFixed(digits);
