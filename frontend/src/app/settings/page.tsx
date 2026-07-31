"use client";

import {
  api,
  IS_STATIC,
  type Credential,
  type Preferences,
  type RefreshStatus,
} from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

export default function SettingsPage() {
  // 정적 배포에는 백엔드가 없으므로 설정 화면 자체가 동작할 수 없습니다.
  // 빈 화면이나 알 수 없는 오류 대신 이유와 대안을 말합니다.
  if (IS_STATIC) {
    return (
      <>
        <h2>설정</h2>
        <div className="banner info">
          <strong>정적 배포에서는 설정을 사용할 수 없습니다</strong>
          GitHub Pages 는 정적 파일만 서빙하므로 인증정보를 저장할 백엔드가
          없습니다. 한국(KRX) 데이터를 포함하려면 저장소의{" "}
          <code>Settings → Secrets and variables → Actions</code> 에{" "}
          <code>KRX_AUTH_KEY</code> secret 을 추가하십시오 — 다음 배포부터
          반영됩니다. 로컬 실행(백엔드 포함)에서는 이 화면에서 직접 입력할 수
          있습니다.
        </div>
      </>
    );
  }
  return <SettingsInner />;
}

function SettingsInner() {
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

      <AISettings />
      <AutoRefreshSettings />

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

/**
 * AI 모델 설정.
 *
 * 값을 마스킹하지 않는 이유: 여기 있는 것은 비밀이 아니라 **어떤 모델이
 * 답했는지**입니다. 가려버리면 분석 결과의 출처를 확인할 방법이 없어집니다.
 * API 키만 위쪽 인증정보 영역에서 암호화 저장됩니다.
 */
function AISettings() {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [envLocked, setEnvLocked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [msg, setMsg] = useState<{ tone: "info" | "warn"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.preferences();
      setPrefs(r.preferences);
      setEnvLocked(r.env_controlled);
      setNote(r.note);
      setModel(r.preferences.ai_model);
      setBaseUrl(r.preferences.ai_base_url);
    } catch (e) {
      setMsg({ tone: "warn", text: String(e instanceof Error ? e.message : e) });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    try {
      const r = await api.savePreferences({ ai_model: model, ai_base_url: baseUrl });
      setPrefs(r.preferences);
      setMsg({ tone: "info", text: "AI 설정이 저장되었습니다." });
    } catch (e) {
      setMsg({ tone: "warn", text: String(e instanceof Error ? e.message : e) });
    }
  }

  if (!prefs) return null;
  const locked = envLocked.includes("ai_model");

  return (
    <>
      <h3>AI 분석 설정</h3>
      {msg && (
        <div className={`banner ${msg.tone}`}>
          <strong>{msg.tone === "info" ? "완료" : "오류"}</strong>
          {msg.text}
        </div>
      )}
      <div className="card">
        <div className="row">
          <label style={{ minWidth: 90, fontSize: 12 }} className="muted">
            모델 이름
          </label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={locked}
            placeholder="gpt-5.6-luna"
            style={{ maxWidth: 320 }}
          />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <label style={{ minWidth: 90, fontSize: 12 }} className="muted">
            엔드포인트
          </label>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={envLocked.includes("ai_base_url")}
            placeholder="https://api.openai.com/v1"
            style={{ maxWidth: 380 }}
          />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={save} disabled={locked}>
            저장
          </button>
        </div>
        <div className="caveat" style={{ marginTop: 10 }}>
          {note}
        </div>
        {locked && (
          <div className="caveat">
            <code>AI_MODEL</code> 환경변수가 설정되어 있어 그쪽이 우선합니다.
            여기서 고쳐도 반영되지 않으므로 입력을 막아두었습니다.
          </div>
        )}
        <div className="caveat">
          AI 분석은 위의 <code>OPENAI_API_KEY</code> 가 있어야 동작합니다. 호출은
          버튼을 눌렀을 때만 나가며 사용자 계정에 과금됩니다. 결과는 전부{" "}
          <a href="/ai-logs">AI 분석 기록</a>에 저장됩니다.
        </div>
      </div>
    </>
  );
}

/** 주가 자동 수집. 기본이 꺼짐인 이유는 켜는 순간 외부 호출이 시작되기 때문입니다. */
function AutoRefreshSettings() {
  const [status, setStatus] = useState<RefreshStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.refreshStatus());
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }, []);

  useEffect(() => {
    load();
    // 갱신이 도는 동안 상태가 바뀌므로 주기적으로 다시 읽습니다.
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  async function patch(update: Record<string, unknown>) {
    try {
      await api.savePreferences(update);
      await load();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }

  async function runNow() {
    setBusy(true);
    setMsg(null);
    try {
      const results = await api.runRefresh();
      setMsg(
        results
          .map((r) => `${r.market}: ${r.ok ? `${r.rows.toLocaleString()}행` : "실패"} — ${r.detail}`)
          .join(" / ") || "갱신 대상 시장이 설정되어 있지 않습니다.",
      );
      await load();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;

  return (
    <>
      <h3>주가 자동 수집</h3>
      <div className="card">
        <div className="row">
          <label className="row" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={status.enabled}
              onChange={(e) => patch({ auto_refresh_enabled: e.target.checked })}
              style={{ width: "auto" }}
            />
            자동 갱신 사용
          </label>
          <select
            value={status.interval_minutes}
            onChange={(e) =>
              patch({ auto_refresh_interval_minutes: Number(e.target.value) })
            }
          >
            {[15, 30, 60, 180, 360, 720, 1440].map((m) => (
              <option key={m} value={m}>
                {m >= 60 ? `${m / 60}시간마다` : `${m}분마다`}
              </option>
            ))}
          </select>
          {(["US", "KR"] as const).map((m) => (
            <label className="row" key={m} style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={status.markets.includes(m)}
                onChange={(e) =>
                  patch({
                    auto_refresh_markets: e.target.checked
                      ? [...status.markets, m]
                      : status.markets.filter((x) => x !== m),
                  })
                }
                style={{ width: "auto" }}
              />
              {m === "US" ? "미국" : "한국"}
            </label>
          ))}
          <button onClick={runNow} disabled={busy || status.running}>
            {busy || status.running ? "갱신 중…" : "지금 갱신"}
          </button>
        </div>

        <table style={{ marginTop: 12 }}>
          <tbody>
            <tr>
              <td className="muted">마지막 실행</td>
              <td className="num">{fmtTime(status.last_finished_at)}</td>
            </tr>
            <tr>
              <td className="muted">다음 예정</td>
              <td className="num">
                {status.enabled ? fmtTime(status.next_run_at) : "자동 갱신 꺼짐"}
              </td>
            </tr>
            {status.results.map((r) => (
              <tr key={r.market + r.at}>
                <td className="muted">{r.market} 결과</td>
                <td className={`num ${r.ok ? "pos" : "neg"}`}>
                  {r.ok ? `${r.rows.toLocaleString()}행 / ${r.tickers}종목` : "실패"}
                  <span className="muted"> — {r.detail}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {msg && <div className="caveat">{msg}</div>}
        <div className="caveat">{status.note}</div>
        <div className="caveat">
          기본이 꺼짐인 이유: 켜는 순간부터 주기적으로 외부 데이터 소스를
          호출합니다. 무료 소스(yfinance)는 과도한 요청에 조용히 차단으로
          응답하므로, 주기를 필요 이상으로 짧게 두지 마십시오. 일별 확정
          데이터라 하루에 몇 번이면 충분합니다.
        </div>
      </div>
    </>
  );
}

function fmtTime(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}
