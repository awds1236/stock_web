"use client";

import { setApiBase } from "@/lib/backend";
import { useBackend, useBackendProbe, useRelativeTime } from "@/lib/useBackend";
import Link from "next/link";
import { useState } from "react";

/**
 * 지금 무엇을 보고 있는지 항상 알려주는 막대.
 *
 * 이 앱에서 가장 중요한 UI 요소입니다. 화면의 숫자가 **지금 값인지 구운
 * 값인지** 구분되지 않으면, 사용자는 낡은 데이터를 최신으로 오해합니다.
 * 그건 이 앱이 낼 수 있는 가장 나쁜 오류입니다 -- 조용하고, 발견되지
 * 않으며, 판단에 직접 영향을 줍니다.
 */
export function BackendBar() {
  useBackendProbe();
  const backend = useBackend();
  const ago = useRelativeTime(backend.checkedAt);
  const [connecting, setConnecting] = useState(false);
  const [url, setUrl] = useState("");

  if (backend.mode === "live") {
    return (
      <div className="statusbar live">
        <span className="dot" />
        <span>
          <strong>실시간</strong> — 백엔드 연결됨
          {backend.base ? ` (${backend.base})` : " (같은 출처)"}
        </span>
        <span className="muted">확인 {ago}</span>
        <button className="ghost tiny" onClick={() => setApiBase("")}>
          연결 해제
        </button>
      </div>
    );
  }

  return (
    <div className="statusbar snapshot">
      <span className="dot" />
      <span>
        {backend.mode === "snapshot" ? (
          <>
            <strong>스냅샷</strong> — 분석은 CI 가 구운 값입니다. 가격만 브라우저가
            직접 갱신합니다.
          </>
        ) : (
          <>
            <strong>데이터 없음</strong> — 백엔드도 스냅샷도 없습니다.
          </>
        )}
      </span>
      {connecting ? (
        <span className="row" style={{ gap: 6 }}>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://내-백엔드-주소"
            style={{ width: 240, height: 26, fontSize: 12 }}
            onKeyDown={(e) => {
              if (e.key === "Enter") setApiBase(url).then(() => setConnecting(false));
            }}
          />
          <button
            className="tiny"
            onClick={() => setApiBase(url).then(() => setConnecting(false))}
          >
            연결
          </button>
          <button className="ghost tiny" onClick={() => setConnecting(false)}>
            취소
          </button>
        </span>
      ) : (
        <>
          <button className="ghost tiny" onClick={() => setConnecting(true)}>
            백엔드 연결
          </button>
          <Link href="/settings" className="muted" style={{ fontSize: 11 }}>
            설정에서 자세히 →
          </Link>
        </>
      )}
    </div>
  );
}
