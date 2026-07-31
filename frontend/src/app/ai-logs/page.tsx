"use client";

import { Markdown } from "@/components/Markdown";
import { api, IS_STATIC, type AILog } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

/** 목록 미리보기용. `##` 나 `**` 가 그대로 보이면 읽기 어렵습니다. */
function plain(md: string | null): string {
  return (md ?? "")
    .replace(/[#*`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const KIND_LABEL: Record<string, string> = {
  stock: "종목",
  sector: "업종",
  market: "시장",
};

/**
 * AI 분석 이력.
 *
 * 왜 별도 화면이 필요한가: AI 호출은 유료이고 **같은 데이터로 다시 물어도 같은
 * 문장이 나오지 않습니다.** 화면을 닫으면 사라지는 구조라면 그건 매번 새로
 * 사야 하는 결과입니다. 여기서 과거 분석을 그대로 다시 읽을 수 있고, 그때
 * 모델에게 넘긴 숫자까지 볼 수 있습니다 -- 나중에 "무엇을 근거로 그렇게 썼나"를
 * 확인할 수 없다면 AI 서술은 검증 불가능한 인상으로만 남습니다.
 */
export default function AILogsPage() {
  if (IS_STATIC) {
    return (
      <>
        <h2>AI 분석 기록</h2>
        <div className="banner info">
          <strong>정적 배포에서는 사용할 수 없습니다</strong>
          AI 분석은 API 키와 백엔드가 필요합니다. 기록은 로컬 실행의 데이터베이스에
          저장되며, GitHub Pages 스냅샷에는 포함되지 않습니다.
        </div>
      </>
    );
  }
  return <Inner />;
}

function Inner() {
  const [logs, setLogs] = useState<AILog[]>([]);
  const [selected, setSelected] = useState<AILog | null>(null);
  const [kind, setKind] = useState("");
  const [market, setMarket] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showFacts, setShowFacts] = useState(false);

  const load = useCallback(async () => {
    try {
      setLogs(await api.aiLogs({ kind: kind || undefined, market: market || undefined }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [kind, market]);

  useEffect(() => {
    load();
  }, [load]);

  // 다른 화면의 "기록 보기" 링크(?id=...)로 들어온 경우 그 항목을 바로 엽니다.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) api.aiLog(id).then(setSelected).catch(() => {});
  }, []);

  async function open(id: string) {
    setShowFacts(false);
    try {
      setSelected(await api.aiLog(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove(id: string) {
    await api.deleteAiLog(id);
    if (selected?.id === id) setSelected(null);
    await load();
  }

  return (
    <>
      <h2>AI 분석 기록</h2>
      <p className="sub">
        실행한 AI 분석은 전부 저장됩니다. 결과 문장뿐 아니라{" "}
        <strong>그때 모델에게 넘긴 숫자</strong>도 함께 남기므로, 나중에 무엇을
        근거로 그렇게 썼는지 확인할 수 있습니다.
      </p>

      <div className="card">
        <div className="row">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">전체 종류</option>
            <option value="stock">종목</option>
            <option value="sector">업종</option>
            <option value="market">시장</option>
          </select>
          <select value={market} onChange={(e) => setMarket(e.target.value)}>
            <option value="">전체 시장</option>
            <option value="US">미국</option>
            <option value="KR">한국</option>
          </select>
          <button className="ghost" onClick={load}>
            새로고침
          </button>
          <span className="muted">{logs.length}건</span>
        </div>
      </div>

      {error && (
        <div className="banner warn">
          <strong>불러올 수 없습니다</strong>
          {error}
        </div>
      )}

      {selected && (
        <>
          <h3>
            {selected.subject_label ?? selected.subject}
            <span className="muted">
              {" "}
              · {KIND_LABEL[selected.kind] ?? selected.kind} · {selected.market}
            </span>
          </h3>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted" style={{ fontSize: 12 }}>
                {new Date(selected.created_at + "Z").toLocaleString()} ·{" "}
                {selected.model ?? "—"}
                {selected.duration_ms ? ` · ${(selected.duration_ms / 1000).toFixed(1)}초` : ""}
                {selected.prompt_tokens
                  ? ` · 입력 ${selected.prompt_tokens} / 출력 ${selected.completion_tokens ?? "—"} 토큰`
                  : ""}
              </span>
              <button className="ghost" onClick={() => setSelected(null)}>
                닫기
              </button>
            </div>

            {selected.error ? (
              <div className="banner warn" style={{ marginTop: 10 }}>
                <strong>이 호출은 실패했습니다</strong>
                {selected.error}
              </div>
            ) : (
              <div style={{ marginTop: 10 }}>
                <Markdown text={selected.content ?? ""} />
              </div>
            )}

            {selected.facts && (
              <div style={{ marginTop: 12 }}>
                <button className="ghost" onClick={() => setShowFacts((v) => !v)}>
                  {showFacts ? "근거 숫자 숨기기" : "이 분석이 받은 숫자 보기"}
                </button>
                {showFacts && (
                  <pre
                    style={{
                      marginTop: 8,
                      maxHeight: 380,
                      overflow: "auto",
                      background: "var(--panel-2)",
                      padding: 10,
                      borderRadius: 6,
                      fontSize: 11,
                      lineHeight: 1.5,
                    }}
                  >
                    {JSON.stringify(selected.facts, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        </>
      )}

      <h3>목록</h3>
      {logs.length === 0 ? (
        <div className="card muted">
          아직 실행한 AI 분석이 없습니다. 종목·업종·시장 화면의 &quot;AI
          분석&quot; 버튼으로 시작하십시오.
        </div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>시각</th>
                <th>대상</th>
                <th>종류</th>
                <th>미리보기</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id}>
                  <td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                    {new Date(l.created_at + "Z").toLocaleString()}
                  </td>
                  <td>{l.subject_label ?? l.subject}</td>
                  <td className="muted">
                    {KIND_LABEL[l.kind] ?? l.kind} · {l.market}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {l.error ? (
                      <span className="neg">실패: {l.error.slice(0, 80)}</span>
                    ) : (
                      <span className="muted">{plain(l.content).slice(0, 90)}</span>
                    )}
                  </td>
                  <td className="num" style={{ whiteSpace: "nowrap" }}>
                    <button className="ghost" onClick={() => open(l.id)}>
                      보기
                    </button>{" "}
                    <button className="ghost" onClick={() => remove(l.id)}>
                      삭제
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
