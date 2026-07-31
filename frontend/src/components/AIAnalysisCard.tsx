"use client";

import { Markdown } from "@/components/Markdown";
import { api, IS_STATIC, type AILog, type AIStatus } from "@/lib/api";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

/**
 * AI 서술 분석 카드 -- 종목·섹터·시장 화면이 공유합니다.
 *
 * 설계 규칙:
 *   * **자동으로 실행하지 않습니다.** 호출마다 사용자 계정에 과금되므로,
 *     화면에 들어온 것만으로 돈이 나가면 안 됩니다. 버튼이 유일한 방아쇠입니다.
 *   * **키가 없을 때 버튼을 숨기지 않고 이유를 말합니다.** 버튼이 사라지면
 *     사용자는 기능이 없는 줄 압니다.
 *   * **직전 분석을 먼저 보여줍니다.** 같은 종목을 다시 열었을 때 다시
 *     호출하게 만들면 같은 내용에 두 번 과금됩니다.
 */
export function AIAnalysisCard({
  title,
  subject,
  run,
  historyQuery,
}: {
  title: string;
  subject: string;
  run: () => Promise<AILog>;
  historyQuery: { kind: string; market: string; subject: string };
}) {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [log, setLog] = useState<AILog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPrevious = useCallback(async () => {
    setLog(null);
    setError(null);
    if (IS_STATIC) return;
    try {
      setStatus(await api.aiStatus());
    } catch {
      /* 상태 조회 실패는 치명적이지 않습니다 -- 버튼은 그대로 둡니다 */
    }
    try {
      const [latest] = await api.aiLogs(historyQuery);
      if (latest && !latest.error) setLog(await api.aiLog(latest.id));
    } catch {
      /* 이력이 없을 수도 있습니다 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyQuery.kind, historyQuery.market, historyQuery.subject]);

  useEffect(() => {
    loadPrevious();
  }, [loadPrevious]);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      setLog(await run());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (IS_STATIC) {
    return (
      <div className="card">
        <strong>{title}</strong>
        <div className="caveat" style={{ marginTop: 8 }}>
          AI 분석은 API 키가 필요하므로 정적 배포(GitHub Pages)에서는 실행할 수
          없습니다. 로컬 실행에서 설정에 키를 넣으면 사용할 수 있습니다.
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {status?.model ?? "—"}
        </span>
      </div>

      {status && !status.configured && (
        <div className="caveat" style={{ marginTop: 8 }}>
          {status.message} <Link href="/settings">설정으로 이동 →</Link>
        </div>
      )}

      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={analyze} disabled={busy || status?.configured === false}>
          {busy ? "분석 중… (10~40초)" : log ? "다시 분석" : `${subject} AI 분석`}
        </button>
        {log && (
          <Link className="btn ghost" href={`/ai-logs?id=${log.id}`}>
            기록 보기
          </Link>
        )}
      </div>

      {error && (
        <div className="banner warn" style={{ marginTop: 10 }}>
          <strong>AI 분석 실패</strong>
          {error}
        </div>
      )}

      {log?.content && (
        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            {new Date(log.created_at + "Z").toLocaleString()} · {log.model}
            {log.completion_tokens ? ` · 출력 ${log.completion_tokens} 토큰` : ""}
          </div>
          <Markdown text={log.content} />
        </div>
      )}

      {status?.disclaimer && (
        <div className="caveat" style={{ marginTop: 10 }}>
          {status.disclaimer}
        </div>
      )}
    </div>
  );
}
