"use client";

import { api, type Credential } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

export default function SettingsPage() {
  const [creds, setCreds] = useState<Credential[]>([]);
  const [warning, setWarning] = useState("");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ tone: "info" | "warn"; text: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    try {
      const r = await api.credentials();
      setCreds(r.credentials);
      setWarning(r.warning);
    } catch (e) {
      setMsg({ tone: "warn", text: String(e instanceof Error ? e.message : e) });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function save(name: string) {
    const value = (inputs[name] ?? "").trim();
    if (!value) return;
    try {
      await api.setCredential(name, value);
      setInputs((s) => ({ ...s, [name]: "" }));
      setMsg({ tone: "info", text: `${name} 저장되었습니다.` });
      await load();
    } catch (e) {
      setMsg({ tone: "warn", text: String(e instanceof Error ? e.message : e) });
    }
  }

  async function remove(name: string) {
    try {
      await api.deleteCredential(name);
      setMsg({ tone: "info", text: `${name} 삭제되었습니다.` });
      await load();
    } catch (e) {
      setMsg({ tone: "warn", text: String(e instanceof Error ? e.message : e) });
    }
  }

  return (
    <>
      <h2>설정</h2>
      <p className="sub">
        인증정보는 앱 안에 암호화되어 저장됩니다. 저장소(git)에는 들어가지
        않습니다.
      </p>

      {msg && (
        <div className={`banner ${msg.tone}`}>
          <strong>{msg.tone === "info" ? "완료" : "오류"}</strong>
          {msg.text}
        </div>
      )}

      {warning && (
        <div className="banner warn">
          <strong>배포 시 주의</strong>
          {warning}
        </div>
      )}

      {creds.map((c) => (
        <div className="card" key={c.name}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{c.label}</strong>
            <span className={c.configured ? "pos" : "muted"}>
              {c.configured
                ? c.source === "env"
                  ? "환경변수로 설정됨"
                  : "설정됨"
                : "미설정"}
            </span>
          </div>

          <div className="caveat" style={{ marginTop: 8 }}>
            {c.help}{" "}
            <a href={c.signup_url} target="_blank" rel="noreferrer">
              발급 안내 →
            </a>
          </div>

          {c.configured && (
            <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              현재 값: <code>{c.masked}</code>
              <span style={{ marginLeft: 8 }}>
                (전체 값은 조회할 수 없습니다)
              </span>
            </div>
          )}

          {c.editable ? (
            <div className="row" style={{ marginTop: 10 }}>
              <input
                type="password"
                placeholder={
                  c.name === "SEC_USER_AGENT"
                    ? "이름 email@example.com"
                    : "인증키 입력"
                }
                value={inputs[c.name] ?? ""}
                onChange={(e) =>
                  setInputs((s) => ({ ...s, [c.name]: e.target.value }))
                }
                style={{ maxWidth: 380 }}
              />
              <button onClick={() => save(c.name)}>저장</button>
              {c.configured && (
                <button className="ghost" onClick={() => remove(c.name)}>
                  삭제
                </button>
              )}
            </div>
          ) : (
            <div className="caveat" style={{ marginTop: 10 }}>
              이 값은 환경변수로 설정되어 있어 환경변수가 우선합니다. 여기서
              수정해도 반영되지 않으므로 입력을 막아두었습니다.
            </div>
          )}
        </div>
      ))}

      <h3>미국 주식은 인증키가 필요 없습니다</h3>
      <div className="card">
        <div>
          시세(yfinance)는 키 없이 동작합니다. 위의 SEC 연락처는{" "}
          <strong>내부자 매매(Form 4)</strong> 조회에만 필요하며, 시세·지표·예측은
          그것 없이도 전부 사용할 수 있습니다.
        </div>
        <div className="caveat" style={{ marginTop: 8 }}>
          SEC 는 API 키를 발급하지 않는 대신 실제 연락 가능한 이메일을 요구합니다.
          이는 인증이 아니라 &quot;누가 요청하는지 밝히라&quot;는 정책입니다.
        </div>
      </div>
    </>
  );
}
