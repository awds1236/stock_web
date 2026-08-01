"use client";

import {
  getStatus,
  startProbing,
  subscribe,
  type BackendStatus,
} from "@/lib/backend";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

/**
 * 백엔드 연결 상태를 구독합니다.
 *
 * 화면이 이 값을 읽어야 하는 이유: 같은 버튼이 백엔드가 붙어 있을 때는
 * 동작하고 없을 때는 안내를 띄워야 하는데, 빌드 시점 상수로는 그 전환을
 * 표현할 수 없습니다. 사용자가 설정에서 주소를 넣는 순간 화면 전체가
 * 새로고침 없이 바뀌어야 합니다.
 */
export function useBackend(): BackendStatus {
  return useSyncExternalStore(subscribe, getStatus, getStatus);
}

/** 앱 전체에서 한 번만 -- 주기적 헬스 체크를 시작합니다. */
export function useBackendProbe(): void {
  useEffect(() => startProbing(), []);
}

/**
 * 주기적으로 다시 불러오기.
 *
 * 이 앱의 화면들은 대부분 "열었을 때 한 번" 만 데이터를 읽었습니다. 그러면
 * 탭을 열어둔 채 시간이 지나면 화면의 숫자가 조용히 낡습니다 -- 사용자는
 * 그것이 낡았다는 사실조차 알 수 없습니다.
 *
 * 두 가지를 지킵니다:
 *   * **탭이 보이지 않으면 쉽니다.** 배경 탭이 종일 열려 있는 것은 흔하고,
 *     그동안의 요청은 전부 낭비입니다. 다시 보이면 즉시 한 번 당겨옵니다.
 *   * **겹쳐 실행하지 않습니다.** 느린 응답이 쌓이면 요청이 중첩됩니다.
 */
export function usePolling(
  fn: () => Promise<void> | void,
  intervalMs: number,
  deps: unknown[] = [],
): void {
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    if (intervalMs <= 0) return;
    let cancelled = false;
    let running = false;

    const tick = async () => {
      if (cancelled || running) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible")
        return;
      running = true;
      try {
        await saved.current();
      } finally {
        running = false;
      }
    };

    const id = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") tick();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);
}

/**
 * 마지막 갱신 시각을 사람이 읽는 상대 시간으로.
 *
 * 절대 시각("14:23:11")만 보여주면 사용자가 직접 뺄셈을 해야 합니다.
 * "12초 전"은 신선한지 아닌지를 즉시 알려줍니다.
 */
export function useRelativeTime(at: number | null): string {
  const [, force] = useState(0);
  useEffect(() => {
    if (at === null) return;
    const id = setInterval(() => force((n) => n + 1), 5_000);
    return () => clearInterval(id);
  }, [at]);

  if (at === null) return "—";
  const secs = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (secs < 5) return "방금";
  if (secs < 60) return `${secs}초 전`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.round(hours / 24)}일 전`;
}
