/**
 * 백엔드 연결 상태 -- **실행 시점에** 결정됩니다.
 *
 * 왜 빌드 시점 상수가 아닌가:
 *
 *   이전 구조는 `NEXT_PUBLIC_STATIC` 한 개로 "정적이냐 아니냐"를 빌드 때 못
 *   박았습니다. 그래서 GitHub Pages 에 올린 사이트는 영원히 스냅샷만 읽었고,
 *   나중에 백엔드를 띄워도 **다시 빌드해서 다시 배포하기 전에는** 살아나지
 *   않았습니다. 배포된 페이지가 멈춰 있는 느낌의 근본 원인이 이것입니다.
 *
 *   지금은 주소를 실행 시점에 찾습니다. 배포된 사이트에서 설정 화면에 백엔드
 *   주소를 넣으면 그 순간부터 모든 화면이 살아있는 데이터를 씁니다. 재빌드도
 *   재배포도 필요 없습니다.
 *
 * 해석 순서:
 *   1. localStorage  -- 사용자가 설정 화면에서 입력한 주소 (가장 강함)
 *   2. NEXT_PUBLIC_API_BASE -- 빌드 시 주입한 기본값
 *   3. 같은 출처     -- 로컬 개발(next dev 의 /api 프록시)
 *
 * 어느 것도 응답하지 않으면 스냅샷으로 물러나되, **살아있는 척하지 않습니다.**
 * 화면은 지금 무엇을 보고 있는지 항상 표시합니다.
 */

export type BackendMode =
  | "live" // 백엔드 응답 확인됨. 전 기능 동작
  | "snapshot" // 백엔드 없음. CI 가 만든 JSON 스냅샷 사용
  | "down"; // 백엔드도 스냅샷도 없음 (로컬에서 백엔드를 안 띄운 경우)

export type BackendStatus = {
  mode: BackendMode;
  base: string; // "" 이면 같은 출처
  checkedAt: number | null;
  error: string | null;
  probing: boolean;
};

/** CI 가 JSON 스냅샷을 함께 배포했는가 (빌드 시점 사실). */
export const HAS_SNAPSHOTS = process.env.NEXT_PUBLIC_STATIC === "1";
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

const ENV_BASE = (process.env.NEXT_PUBLIC_API_BASE || "").replace(/\/$/, "");
const STORAGE_KEY = "stock_web.apiBase";

// 헬스 체크 간격. 백엔드가 죽었다 살아나는 것을 화면이 알아채야 하므로
// 한 번만 확인하고 끝내지 않습니다.
const PROBE_INTERVAL_MS = 60_000;
const PROBE_TIMEOUT_MS = 6_000;

let status: BackendStatus = {
  mode: HAS_SNAPSHOTS ? "snapshot" : "down",
  base: "",
  checkedAt: null,
  error: null,
  probing: false,
};

const listeners = new Set<(s: BackendStatus) => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inFlight: Promise<BackendStatus> | null = null;

function emit() {
  for (const fn of listeners) fn(status);
}

function set(patch: Partial<BackendStatus>) {
  status = { ...status, ...patch };
  emit();
}

/** 사용자가 저장한 백엔드 주소. 서버 렌더링 중에는 localStorage 가 없습니다. */
export function storedApiBase(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // 프라이빗 모드 등에서 접근이 막힐 수 있습니다
  }
}

/**
 * 지금 호출에 쓸 주소 접두사.
 *
 * 빈 문자열은 "같은 출처"라는 뜻입니다 -- 로컬 개발에서 next 의 rewrite 가
 * /api/* 를 백엔드로 넘겨줍니다.
 */
export function apiBase(): string {
  const stored = storedApiBase();
  if (stored !== null) return stored.replace(/\/$/, "");
  if (ENV_BASE) return ENV_BASE;
  // 정적 빌드에는 같은 출처의 /api 가 존재하지 않습니다. 빈 값을 돌려주면
  // Pages 자신에게 요청해 404 HTML 을 JSON 으로 읽으려다 실패합니다.
  return HAS_SNAPSHOTS ? "" : "";
}

/** 주소가 하나라도 지정되었는가. 지정 안 됐고 정적 빌드면 탐색할 백엔드가 없습니다. */
function hasCandidate(): boolean {
  const stored = storedApiBase();
  if (stored !== null) return stored.trim() !== "";
  if (ENV_BASE) return true;
  return !HAS_SNAPSHOTS; // 로컬 개발은 같은 출처를 후보로 봅니다
}

export function getStatus(): BackendStatus {
  return status;
}

export function isLive(): boolean {
  return status.mode === "live";
}

export function subscribe(fn: (s: BackendStatus) => void): () => void {
  listeners.add(fn);
  fn(status);
  return () => {
    listeners.delete(fn);
  };
}

/**
 * 백엔드 주소를 저장하고 즉시 다시 확인합니다.
 *
 * `null` 을 넣으면 저장값을 지우고 기본 해석으로 돌아갑니다. 빈 문자열은
 * "백엔드를 쓰지 않겠다"는 명시적 선택이며 저장값으로 남습니다 -- 기본값이
 * 있는 배포에서 스냅샷만 보고 싶을 때 필요합니다.
 */
export async function setApiBase(value: string | null): Promise<BackendStatus> {
  if (typeof window !== "undefined") {
    try {
      if (value === null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, value.trim().replace(/\/$/, ""));
    } catch {
      /* 저장이 막혀도 이번 세션에서는 동작해야 합니다 */
    }
  }
  return probe(true);
}

/**
 * 헬스 체크.
 *
 * 응답이 200 이고 JSON 이어야 살아있는 것으로 봅니다. 상태 코드만 보면
 * 정적 호스팅이 돌려주는 404 HTML 페이지를 백엔드로 오인할 수 있습니다.
 */
export async function probe(force = false): Promise<BackendStatus> {
  if (inFlight && !force) return inFlight;
  if (!hasCandidate()) {
    set({
      mode: HAS_SNAPSHOTS ? "snapshot" : "down",
      base: "",
      checkedAt: Date.now(),
      error: null,
      probing: false,
    });
    return status;
  }

  const base = apiBase();
  set({ probing: true });

  inFlight = (async () => {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const res = await fetch(`${base}/api/health`, {
        cache: "no-store",
        signal: controller.signal,
      });
      const body = await res.json();
      if (!res.ok || body?.status !== "ok") throw new Error(`HTTP ${res.status}`);
      set({ mode: "live", base, checkedAt: Date.now(), error: null, probing: false });
    } catch (e) {
      set({
        mode: HAS_SNAPSHOTS ? "snapshot" : "down",
        base,
        checkedAt: Date.now(),
        error: describe(e, base),
        probing: false,
      });
    } finally {
      clearTimeout(t);
      inFlight = null;
    }
    return status;
  })();

  return inFlight;
}

function describe(e: unknown, base: string): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (msg.includes("abort"))
    return `백엔드가 ${PROBE_TIMEOUT_MS / 1000}초 안에 응답하지 않았습니다 (${base || "같은 출처"}).`;
  // CORS 차단과 네트워크 실패는 브라우저에서 구분되지 않습니다. 둘 다
  // "Failed to fetch" 로 옵니다 -- 그래서 두 가능성을 같이 적습니다.
  if (/failed to fetch|networkerror|load failed/i.test(msg))
    return (
      `백엔드에 연결하지 못했습니다 (${base || "같은 출처"}). 주소가 맞는지, ` +
      `그리고 백엔드가 이 사이트의 출처를 CORS_ORIGINS 로 허용했는지 확인하십시오.`
    );
  return `백엔드 확인 실패 (${base || "같은 출처"}): ${msg}`;
}

/**
 * 첫 확인이 끝날 때까지 기다립니다.
 *
 * 왜 필요한가: 확인이 끝나기 전에는 모드가 기본값(스냅샷)입니다. 그 상태로
 * 데이터를 읽으면 **백엔드가 살아 있는 배포에서도 첫 화면이 스냅샷을 한 번
 * 요청**하고, 스냅샷이 없는 배포에서는 "가져올 곳이 없습니다" 오류가 잠깐
 * 스쳤다가 사라집니다 (실측 확인). 읽기 전에 한 번만 기다리면 둘 다 없어집니다.
 *
 * 이미 확인이 끝났으면 즉시 반환하므로 이후 요청은 지연되지 않습니다.
 */
export function ready(): Promise<BackendStatus> {
  if (status.checkedAt !== null) return Promise.resolve(status);
  return probe();
}

/** 주기적 재확인 시작. 앱 레이아웃에서 한 번만 호출합니다. */
export function startProbing(): () => void {
  probe();
  if (timer === null && typeof window !== "undefined") {
    timer = setInterval(() => {
      // 탭이 숨겨져 있으면 확인하지 않습니다. 백그라운드 탭이 종일 열려 있는
      // 것이 흔한데, 그동안 계속 요청하면 서버 로그만 채웁니다.
      if (document.visibilityState === "visible") probe(true);
    }, PROBE_INTERVAL_MS);
  }
  return () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}
