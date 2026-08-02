/**
 * 백엔드 API 클라이언트.
 *
 * 설계 규칙: 예측(`Forecast`)에서 `quality` 는 **선택 필드가 아닙니다.**
 * 예측 확률만 떼어내 화면에 띄우는 것이 타입 수준에서 불가능해야 합니다.
 * 보정되지 않은 확률은 과신을 낳고, 과신하는 예측은 통상적 베팅 규칙 하에서
 * 장기 성장률을 음수로 만듭니다.
 */

import { apiBase, BASE_PATH, HAS_SNAPSHOTS, isLive, ready } from "@/lib/backend";

export type Coverage = {
  market: string;
  n_tickers: number;
  first_date: string | null;
  last_date: string | null;
  n_rows: number;
  ready: boolean;
  needs_credential: string | null;
  n_days: number;
  history_note: string | null;
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

/**
 * 대상 단위 분석 리포트.
 *
 * `report` 의 내부는 백엔드 `app/reports.py` 가 만드는 dict 그대로입니다.
 * 필드를 하나하나 복제한 타입을 두지 않는 이유: 백엔드가 지표를 하나 추가할
 * 때마다 두 곳을 고쳐야 하고, 안 고치면 값이 있는데 화면에 없는 상태가
 * 됩니다. 화면이 실제로 읽는 부분만 좁게 선언합니다.
 */
export type RuleBlock = {
  matched: string[];
  score: number;
  all: string[];
  caveat: string;
};

export type StockReport = {
  market: string;
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  as_of: string;
  price: {
    close: number | null;
    prev_close: number | null;
    change_1d: number | null;
    high_52w: number | null;
    low_52w: number | null;
  };
  returns: Record<string, number | null>;
  indicators: Record<string, number | null>;
  trend: {
    stack: string;
    cross_20_60: CrossState;
    cross_50_200: CrossState;
  };
  liquidity: {
    value_5d: number | null;
    value_60d: number | null;
    surge_ratio: number | null;
  };
  levels: Level[];
  zones: Zones;
  plan: Plan;
  regime: Regime;
  scenarios: Scenario[];
  interpretation: Interpretation[];
  rules: RuleBlock;
  relative: {
    sector: string | null;
    sector_ret_20d: number | null;
    stock_ret_20d: number | null;
    excess_vs_sector_20d: number | null;
    universe_ret_20d: number | null;
    excess_vs_universe_20d: number | null;
    rank_60d: { rank: number; of: number } | null;
  };
  data_quality: {
    n_days: number;
    first_date: string;
    last_date: string;
    n_universe: number;
  };
  caveats: string[];
};

/** 지지·저항은 선이 아니라 근거가 겹치는 **구간**입니다 (backend/app/indicators/zones.py). */
export type Zone = {
  kind: "support" | "resistance";
  price: number;
  low: number;
  high: number;
  distance_pct: number;
  distance_atr: number;
  score: number;
  confidence: "높음" | "보통" | "낮음";
  n_methods: number;
  methods: string[];
  evidence: { method: string; price: number; quality: number; detail: string }[];
  touch_prob_21d: number | null;
  sigma_days: number | null;
};

export type Zones = {
  as_of: string | null;
  spot: number | null;
  atr_14?: number | null;
  atr_pct?: number | null;
  vol_annual: number | null;
  horizon_days: number;
  supports: Zone[];
  resistances: Zone[];
  method_notes: { method: string; evidence_weight: number; basis: string }[];
  insufficient: boolean;
  note: string;
};

export type PlanStep = {
  label: string;
  kind: "market" | "limit";
  price: number;
  low: number;
  high: number;
  weight: number;
  distance_pct: number;
  touch_prob_21d: number | null;
  confidence: string | null;
  why: string;
};

export type Plan =
  | { available: false; reason: string }
  | {
      available: true;
      horizon_days: number;
      spot: number;
      atr_14: number;
      entry: {
        steps: PlanStep[];
        weighting: string;
        weighting_basis: string;
        avg_cost_if_all_filled: number | null;
        partial_fills: {
          filled_steps: number;
          through: string;
          capital_used: number;
          capital_idle: number;
          avg_cost: number | null;
        }[];
        prob_no_limit_fill_21d: number | null;
      };
      invalidation: {
        price: number;
        basis: string;
        distance_pct: number | null;
        meaning: string;
      };
      exit: {
        steps: PlanStep[];
        weighting_basis: string;
        weight_sold_if_all_reached: number;
        weight_still_held: number;
        avg_exit_if_all_reached: number | null;
        why_split: string;
      };
      risk: {
        risk_per_position_pct: number | null;
        reward_to_risk: number | null;
        max_position_for_1pct_account_risk: number | null;
        basis: string;
      };
      caveats: string[];
    };

export type Regime = {
  trend: {
    label: string;
    basis: string;
    anchor_window?: number;
    anchor_slope_20d?: number | null;
    gap_from_anchor_atr?: number | null;
    caveat?: string;
  };
  volatility: {
    label: string;
    vol_20d?: number | null;
    percentile_2y?: number | null;
    ratio_20d_over_60d?: number | null;
    atr_14?: number | null;
    atr_pct?: number | null;
    caveat?: string;
  };
  range_position: {
    value: number | null;
    high: number | null;
    low: number | null;
    window_days: number;
  } | null;
  summary: string | null;
};

export type Scenario = {
  name: string;
  trigger: string;
  touch_prob: number | null;
  prob_note?: string;
  then: string;
  invalidated_by: string;
  horizon_days: number;
};

export type CrossState = {
  state: "golden" | "dead" | "insufficient";
  last_cross: "golden" | "dead" | null;
  days_since_cross: number | null;
};

export type StockAnalysis = {
  report: StockReport;
  forecast: Forecast | null;
  forecast_error: string | null;
};

export type TickerLine = {
  ticker: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
  close: number | null;
  ret_5d?: number | null;
  ret_20d: number | null;
  ret_60d?: number | null;
};

export type SectorReport = {
  market: string;
  level: string;
  sector: string;
  as_of: string;
  ret_20d: number | null;
  ret_60d: number | null;
  relative_strength_60d: number | null;
  breadth: number | null;
  n_constituents: number;
  rank_by_ret_20d: number | null;
  n_sectors: number;
  leaders: TickerLine[];
  laggards: TickerLine[];
  market_ret_20d: number | null;
  peer_sectors: SectorRow[];
  regime: Regime & { caveat?: string };
  lead_lag: {
    available: boolean;
    reason?: string | null;
    lag_weeks?: number;
    significance_threshold?: number;
    led_by: LeadLagPair[];
    leads: LeadLagPair[];
    verdict?: string;
    note?: string;
  };
  concentration:
    | { available: false; reason: string }
    | {
        available: true;
        mean_ret_20d: number | null;
        top_mean_ret_20d: number | null;
        ex_top_ret_20d: number | null;
        top_k: number;
        n: number;
        note: string;
      };
  caveats: string[];
};

export type AttentionItem = {
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

export type MarketReport = {
  market: string;
  as_of: string;
  universe: { n_tickers: number; note: string };
  index_proxy: Record<string, number | null>;
  internals: {
    above_sma60_pct: number | null;
    near_52w_high_pct: number | null;
    median_vol_20d: number | null;
    n_evaluated: number;
  };
  regime: MarketRegime;
  sectors_top: SectorRow[];
  sectors_bottom: SectorRow[];
  level: string;
  movers_up: TickerLine[];
  movers_down: TickerLine[];
  attention: AttentionItem[];
  rules: string[];
  caveats: string[];
};

/** 평균 수익률이 못 보여주는 것 — 소수 종목이 끌었는가, 종목 선택이 통하는가. */
export type MarketRegime = {
  concentration: {
    market_ret_20d: number | null;
    ex_top5_ret_20d: number | null;
    top5_mean_ret_20d: number | null;
    n: number;
  } | null;
  dispersion_20d: number | null;
  avg_pair_correlation_60d: number | null;
  summary: string | null;
  caveat?: string;
};

export type LeaderRow = {
  sector: string;
  ret_5d: number | null;
  ret_20d: number | null;
  ret_60d: number | null;
  excess_5d: number | null;
  excess_20d: number | null;
  excess_60d: number | null;
  breadth: number | null;
  participation_20d: number | null;
  n_constituents: number;
  leadership_score: number | null;
};

export type LeadLagPair = {
  leader: string;
  follower: string;
  corr: number;
  significant: boolean;
};

/** 섹터 주도권·순환. 세 층의 근거 강도가 다르므로 화면도 따로 보여줍니다. */
export type Leadership = {
  market: string;
  level: string;
  as_of: string;
  n_universe: number;
  leaders:
    | { available: false; reason: string }
    | {
        available: true;
        as_of: string;
        n_sectors: number;
        leading: LeaderRow[];
        lagging: LeaderRow[];
        score_note: string;
      };
  persistence: {
    available: boolean;
    reason?: string;
    lookback_days: number;
    horizon_days: number;
    n_periods?: number;
    rank_ic?: number | null;
    t_stat?: number | null;
    top_minus_bottom?: number | null;
    verdict?: string;
    caveat?: string;
  };
  lead_lag: {
    available: boolean;
    reason?: string;
    lag_weeks: number;
    n_weeks?: number;
    n_sectors?: number;
    n_pairs_tested?: number;
    significance_threshold?: number;
    n_significant?: number;
    pairs?: LeadLagPair[];
    method?: string;
    verdict?: string;
    caveat?: string;
  };
  rotation: {
    available: boolean;
    reason: string | null;
    candidates: {
      sector: string;
      led_by: string;
      corr: number;
      direction: string;
      excess_20d: number | null;
      condition: string;
    }[];
    persistence_verdict?: string;
    how_to_read?: string;
    what_this_means?: string;
  };
  market_regime: MarketRegime;
  caveats: string[];
};

export type Quote = {
  market: string;
  ticker: string;
  price: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  currency: string | null;
  source: "live" | "stored";
  as_of: string | null;
  note: string;
};

export type AILog = {
  id: string;
  created_at: string;
  kind: "stock" | "sector" | "market";
  market: string;
  subject: string;
  subject_label: string | null;
  model: string | null;
  content: string | null;
  facts: Record<string, unknown> | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  duration_ms: number | null;
  error: string | null;
};

export type AIStatus = {
  configured: boolean;
  model: string;
  base_url: string;
  n_logs: number;
  message: string;
  disclaimer: string;
};

export type Preferences = {
  ai_model: string;
  ai_base_url: string;
  ai_max_output_tokens: number;
  ai_timeout_seconds: number;
  auto_refresh_enabled: boolean;
  auto_refresh_interval_minutes: number;
  auto_refresh_markets: string[];
  auto_refresh_lookback_days: number;
};

export type RefreshResult = {
  market: string;
  at: string;
  ok: boolean;
  rows: number;
  tickers: number;
  detail: string;
};

export type RefreshStatus = {
  enabled: boolean;
  interval_minutes: number;
  markets: string[];
  lookback_days: number;
  running: boolean;
  last_started_at: string | null;
  last_finished_at: string | null;
  next_run_at: string | null;
  results: RefreshResult[];
  note: string;
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
 * 읽기 경로는 **실행 시점의 연결 상태**가 정합니다 (`lib/backend.ts`).
 *
 *   백엔드 연결됨 -> /api/* 를 호출. 지금 이 순간의 값.
 *   연결 안 됨    -> CI 가 만들어 둔 JSON 스냅샷(/data/*.json).
 *
 * 빌드 때 둘 중 하나로 못 박지 않는 이유: 배포된 사이트에서 나중에 백엔드를
 * 붙일 수 있어야 하기 때문입니다. 설정 화면에 주소를 넣는 순간 재빌드 없이
 * 전 화면이 살아있는 데이터로 바뀝니다.
 *
 * 쓰기 동작(수집·인증정보·AI)은 백엔드 없이는 불가능합니다. 되는 척하는 대신
 * 무엇을 하면 되는지 알려주고 실패합니다.
 */
const BASE = BASE_PATH;
export const STATIC_HORIZON = 21;

const NEEDS_BACKEND_MSG =
  "이 동작에는 백엔드가 필요합니다. 지금은 CI 가 만들어 둔 스냅샷을 보고 " +
  "있습니다. 설정 화면에서 백엔드 주소를 연결하면 이 기능이 켜집니다 " +
  "(재배포 없이 즉시 적용됩니다).";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store", ...init });
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

function needsBackend(): never {
  throw new ApiError(NEEDS_BACKEND_MSG, 501);
}

/** 스냅샷이 아예 없는 배포에서 읽기를 시도했을 때. */
function noSource(what: string): never {
  throw new ApiError(
    `${what} 를 가져올 곳이 없습니다. 백엔드가 실행 중인지 확인하거나, 설정 ` +
      `화면에서 백엔드 주소를 연결하십시오.`,
    503,
  );
}

/**
 * 읽기 경로 선택기.
 *
 * 백엔드가 살아 있으면 항상 그쪽입니다 -- 지금 이 순간의 값이기 때문입니다.
 * 없으면 스냅샷으로 물러나고, 스냅샷조차 없으면 이유를 말하고 실패합니다.
 */
async function read<T>(
  live: () => Promise<T>,
  snapshot: () => Promise<T>,
  what: string,
): Promise<T> {
  await ready();
  if (isLive()) return live();
  if (HAS_SNAPSHOTS) return snapshot();
  return noSource(what);
}

/** 쓰기 경로: 백엔드 없이는 불가능합니다. */
async function write<T>(live: () => Promise<T>): Promise<T> {
  await ready();
  return isLive() ? live() : needsBackend();
}

export const api = {
  coverage: () =>
    read<Coverage[]>(
      () => req("/api/coverage"),
      () => staticFile("coverage.json"),
      "데이터 현황",
    ),
  universe: (market: string) =>
    read<UniverseItem[]>(
      () => req(`/api/universe/${market}`),
      () => staticFile(`universe-${market}.json`),
      `${market} 종목 목록`,
    ),
  stock: (market: string, ticker: string) =>
    read<StockDetail>(
      () => req(`/api/stocks/${market}/${ticker}`),
      () => staticFile(`stocks-${market}-${ticker}.json`),
      `${ticker} 시세`,
    ),
  forecast: async (market: string, target: string, horizon: number) => {
    await ready();
    if (isLive())
      return req<Forecast>(
        `/api/forecast/${market}?target=${target}&horizon_days=${horizon}`,
      );
    if (!HAS_SNAPSHOTS) return noSource("예측");
    // 스냅샷에는 21일 예측만 포함됩니다. 없는 조합을 404 로 흘리는 대신
    // 이유를 말합니다.
    if (horizon !== STATIC_HORIZON)
      throw new ApiError(
        `스냅샷에는 ${STATIC_HORIZON}일 예측만 포함됩니다. 다른 기간은 백엔드를 ` +
          "연결하면 계산할 수 있습니다.",
        404,
      );
    return staticFile<Forecast>(
      `forecast-${market}-${target}-${STATIC_HORIZON}.json`,
    );
  },
  sectors: (market: string, level: "sector" | "industry" = "sector") =>
    read<SectorRow[]>(
      () => req(`/api/sectors/${market}?level=${level}`),
      () =>
        staticFile(
          level === "industry"
            ? `sectors-${market}-industry.json`
            : `sectors-${market}.json`,
        ),
      "섹터 현황",
    ),
  watchlist: (market: string) =>
    read<Watchlist>(
      () => req(`/api/watchlist/${market}`),
      () => staticFile(`watchlist-${market}.json`),
      "관찰 목록",
    ),
  search: async (market: string, q: string, limit = 20) => {
    await ready();
    return isLive()
      ? req<SearchHit[]>(
          `/api/search/${market}?q=${encodeURIComponent(q)}&limit=${limit}`,
        )
      : searchLocally(await api.universe(market), q, limit);
  },
  buildInfo: () =>
    staticFile<{ generated_at: string; note: string }>("build-info.json"),
  credentials: () =>
    write<{ credentials: Credential[]; warning: string }>(() =>
      req("/api/settings/credentials"),
    ),
  setCredential: (name: string, value: string) =>
    write<Credential>(() =>
      req(`/api/settings/credentials/${name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      }),
    ),
  deleteCredential: (name: string) =>
    write<Credential>(() =>
      req(`/api/settings/credentials/${name}`, { method: "DELETE" }),
    ),
  ingest: (market: string, years = 10) =>
    write<{
      market: string;
      rows: number;
      tickers: number;
      start: string | null;
      end: string | null;
      warnings: string[];
    }>(() => req(`/api/ingest/${market}?years=${years}`, { method: "POST" })),

  // ── 대상 단위 분석 ──────────────────────────────────────────────────
  // 스냅샷 배포에서도 동작합니다. CI 가 같은 엔드포인트의 응답을 파일로
  // 고정해두기 때문입니다 -- 두 배포에서 다른 앱이 되지 않게 하는 것이
  // 이 프로젝트의 규칙입니다. 백엔드가 붙어 있으면 자동으로 그쪽을 씁니다.
  analyzeStock: (market: string, ticker: string, includeForecast = false) =>
    read<StockAnalysis>(
      () =>
        req(
          `/api/analyze/stock/${market}/${ticker}?include_forecast=${includeForecast}`,
        ),
      () => staticFile(`analysis-stock-${market}-${ticker}.json`),
      `${ticker} 분석`,
    ),
  analyzeSector: (market: string, sector: string, level = "industry") =>
    read<{ report: SectorReport }>(
      () =>
        req(
          `/api/analyze/sector/${market}?sector=${encodeURIComponent(sector)}` +
            `&level=${level}`,
        ),
      () => staticFile(`analysis-sector-${market}-${sectorSlug(sector)}.json`),
      `${sector} 분석`,
    ),
  analyzeMarket: (market: string) =>
    read<{ report: MarketReport }>(
      () => req(`/api/analyze/market/${market}`),
      () => staticFile(`analysis-market-${market}.json`),
      "시장 분석",
    ),
  leadership: (market: string) =>
    read<{ report: Leadership }>(
      () => req(`/api/leadership/${market}`),
      () => staticFile(`leadership-${market}.json`),
      "섹터 주도권",
    ),

  // ── 현재가 ──────────────────────────────────────────────────────────
  // 백엔드가 있으면 백엔드가, 없으면 브라우저가 직접 시세 소스를 호출합니다
  // (lib/liveQuote.ts). 여기서는 백엔드 경로만 다룹니다.
  quote: (market: string, ticker: string) =>
    write<Quote>(() => req(`/api/quote/${market}/${ticker}`)),
  quotes: (market: string, tickers: string[]) =>
    write<Quote[]>(() =>
      req(`/api/quotes/${market}?tickers=${tickers.map(encodeURIComponent).join(",")}`),
    ),

  // ── AI 분석 ─────────────────────────────────────────────────────────
  aiStatus: () => write<AIStatus>(() => req("/api/ai/status")),
  aiAnalyzeStock: (market: string, ticker: string) =>
    write<AILog>(() =>
      req(`/api/ai/analyze/stock/${market}/${ticker}`, { method: "POST" }),
    ),
  aiAnalyzeSector: (market: string, sector: string, level = "industry") =>
    write<AILog>(() =>
      req(
        `/api/ai/analyze/sector/${market}?sector=${encodeURIComponent(sector)}` +
          `&level=${level}`,
        { method: "POST" },
      ),
    ),
  aiAnalyzeMarket: (market: string) =>
    write<AILog>(() =>
      req(`/api/ai/analyze/market/${market}`, { method: "POST" }),
    ),
  aiAnalyzeRotation: (market: string) =>
    write<AILog>(() =>
      req(`/api/ai/analyze/rotation/${market}`, { method: "POST" }),
    ),
  aiLogs: (params: { kind?: string; market?: string; subject?: string } = {}) =>
    write<AILog[]>(() => {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
      return req(`/api/ai/logs?${q.toString()}`);
    }),
  aiLog: (id: string) => write<AILog>(() => req(`/api/ai/logs/${id}`)),
  deleteAiLog: (id: string) =>
    write<{ deleted: string }>(() =>
      req(`/api/ai/logs/${id}`, { method: "DELETE" }),
    ),

  // ── 설정·자동 갱신 ──────────────────────────────────────────────────
  preferences: () =>
    write<{ preferences: Preferences; env_controlled: string[]; note: string }>(
      () => req("/api/settings/preferences"),
    ),
  savePreferences: (patch: Partial<Preferences>) =>
    write<{ preferences: Preferences; env_controlled: string[]; note: string }>(
      () =>
        req("/api/settings/preferences", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        }),
    ),
  refreshStatus: () => write<RefreshStatus>(() => req("/api/refresh/status")),
  runRefresh: (market?: string) =>
    write<RefreshResult[]>(() =>
      req(`/api/refresh/run${market ? `?market=${market}` : ""}`, {
        method: "POST",
      }),
    ),
};

/**
 * 업종명 → 정적 스냅샷 파일명.
 *
 * 백엔드 `scripts/export_static.py::_slug` 와 **같은 규칙**이어야 합니다.
 * 갈리면 정적 배포에서만 404 가 나고 로컬에서는 재현되지 않습니다.
 *
 * 여기서 `encodeURIComponent` 를 쓰면 안 됩니다. 파일명이
 * `%EA%B1%B4%EC%84%A4` 가 되는데, 그 URL 을 요청하면 웹서버가 디코딩해서
 * '건설' 파일을 찾으므로 항상 404 입니다 -- 실제로 이것 때문에 정적
 * 배포에서 모든 업종의 분석 버튼이 실패했습니다(한국은 한글, 미국은 공백).
 *
 * UTF-8 바이트 단위로 ASCII 만 남기면 URL 디코딩이 항등이 되어 안전합니다.
 */
export function sectorSlug(name: string): string {
  const bytes = new TextEncoder().encode(name);
  let out = "";
  for (const b of bytes) {
    const ch = String.fromCharCode(b);
    out += /[A-Za-z0-9._-]/.test(ch) ? ch : `_${b.toString(16).padStart(2, "0")}`;
  }
  return out;
}

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
