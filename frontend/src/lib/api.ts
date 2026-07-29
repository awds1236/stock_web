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

export type StockDetail = {
  market: string;
  ticker: string;
  name: string | null;
  sector: string | null;
  prices: PricePoint[];
  indicators: { date: string[]; values: Record<string, (number | null)[]> };
  interpretation: Interpretation[];
};

export type ForecastQuality = {
  n_folds: number;
  n_predictions: number;
  oos_r2: number | null;
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
  baseline_momentum_r2: number | null;
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

export const api = {
  coverage: () => req<Coverage[]>("/api/coverage"),
  universe: (market: string) => req<UniverseItem[]>(`/api/universe/${market}`),
  stock: (market: string, ticker: string) =>
    req<StockDetail>(`/api/stocks/${market}/${ticker}`),
  forecast: (market: string, target: string, horizon: number) =>
    req<Forecast>(
      `/api/forecast/${market}?target=${target}&horizon_days=${horizon}`,
    ),
  sectors: (market: string) => req<SectorRow[]>(`/api/sectors/${market}`),
  credentials: () =>
    req<{ credentials: Credential[]; warning: string }>(
      "/api/settings/credentials",
    ),
  setCredential: (name: string, value: string) =>
    req<Credential>(`/api/settings/credentials/${name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }),
  deleteCredential: (name: string) =>
    req<Credential>(`/api/settings/credentials/${name}`, { method: "DELETE" }),
  ingest: (market: string, years = 10) =>
    req<{
      market: string;
      rows: number;
      tickers: number;
      start: string | null;
      end: string | null;
      warnings: string[];
    }>(`/api/ingest/${market}?years=${years}`, { method: "POST" }),
};

export const pct = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || !isFinite(v)
    ? "—"
    : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 4) =>
  v === null || v === undefined || !isFinite(v) ? "—" : v.toFixed(digits);
