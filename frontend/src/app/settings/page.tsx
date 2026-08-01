"use client";

import {
  api,
  type Credential,
  type Preferences,
  type RefreshStatus,
} from "@/lib/api";
import { setApiBase, storedApiBase } from "@/lib/backend";
import { fetchBrowserQuotes } from "@/lib/liveQuote";
import { useBackend, useRelativeTime } from "@/lib/useBackend";
import { useCallback, useEffect, useState } from "react";

export default function SettingsPage() {
  const backend = useBackend();
  return (
    <>
      <h2>설정</h2>
      <p className="sub">
        백엔드 연결은 이 브라우저에만 저장됩니다. 인증정보는 백엔드 안에
        암호화되어 저장되며, 저장소(git)에는 들어가지 않습니다.
      </p>

      {/* 연결 설정은 **항상** 보여야 합니다. 백엔드가 없을 때 이 화면까지
          막아버리면, 백엔드를 연결할 방법 자체가 사라집니다. */}
      <BackendConnection />
      <QuoteDiagnostic />

      {backend.mode === "live" ? (
        <SettingsInner />
      ) : (
        <div className="banner info">
          <strong>나머지 설정은 백엔드가 연결되면 표시됩니다</strong>
          인증정보(KRX·SEC·OpenAI 키)와 자동 수집 설정은 백엔드에 저장됩니다.
          정적 사이트의 자바스크립트에 키를 두면 그 키가 공개되기 때문입니다.
          한국(KRX) 데이터를 스냅샷에 포함하려면 저장소의{" "}
          <code>Settings → Secrets and variables → Actions</code> 에{" "}
          <code>KRX_AUTH_KEY</code> 를 추가하십시오.
        </div>
      )}
    </>
  );
}

/**
 * 백엔드 주소 연결.
 *
 * 이 화면의 핵심입니다. 여기에 주소를 넣으면 **재배포 없이** 전 화면이
 * 살아있는 데이터로 바뀝니다. 주소는 이 브라우저(localStorage)에만 남으므로
 * 공개 사이트에 남의 백엔드 주소가 박히는 일이 없습니다.
 */
function BackendConnection() {
  const backend = useBackend();
  const ago = useRelativeTime(backend.checkedAt);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  // 이 페이지는 빌드 시점에 HTML 로 미리 렌더링됩니다. 그때는 `window` 가
  // 없으므로, 렌더 중에 `window.location.origin` 을 읽으면 서버 HTML 과
  // 브라우저의 첫 렌더가 달라져 하이드레이션이 깨집니다 (실측 확인).
  // 마운트 후에 채웁니다.
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    setUrl(storedApiBase() ?? "");
  }, [backend.mode]);

  async function connect(value: string | null) {
    setBusy(true);
    try {
      await setApiBase(value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3>백엔드 연결</h3>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <strong>
            {backend.mode === "live" ? "연결됨" : "연결 안 됨"}
          </strong>
          <span className="muted" style={{ fontSize: 12 }}>
            마지막 확인 {ago}
          </span>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://내-백엔드-주소 (비우면 같은 출처)"
            style={{ maxWidth: 420 }}
            onKeyDown={(e) => {
              if (e.key === "Enter") connect(url);
            }}
          />
          <button onClick={() => connect(url)} disabled={busy}>
            {busy ? "확인 중…" : "연결"}
          </button>
          <button className="ghost" onClick={() => connect(null)} disabled={busy}>
            기본값으로
          </button>
        </div>

        {backend.error && backend.mode !== "live" && (
          <div className="banner warn" style={{ marginTop: 10 }}>
            <strong>연결 실패</strong>
            {backend.error}
          </div>
        )}

        <div className="caveat" style={{ marginTop: 10 }}>
          주소는 <strong>이 브라우저에만</strong> 저장됩니다. 배포된 사이트에
          박히지 않으므로, 같은 사이트를 여는 다른 사람에게는 영향이 없습니다.
        </div>
        <div className="caveat">
          백엔드가 이 사이트의 출처를 허용해야 합니다. 백엔드 쪽에{" "}
          <code>CORS_ORIGINS={origin || "https://<이 사이트 주소>"}</code> 를
          환경변수로 넣으십시오. 넣지 않으면 브라우저가 요청을 막고, 그 실패는{" "}
          &quot;연결하지 못했습니다&quot; 로만 보입니다 — CORS 차단과 네트워크
          장애는 브라우저에서 구분되지 않기 때문입니다.
        </div>
        <div className="caveat">
          로컬에서 백엔드를 띄웠다면 <code>http://127.0.0.1:8000</code> 입니다.
          다만 이 사이트가 https 라면 브라우저가 http 백엔드 호출을 차단합니다
          (혼합 콘텐츠). 그때는 로컬 프론트엔드(<code>npm run dev</code>)를
          쓰거나 백엔드를 https 로 노출하십시오.
        </div>
      </div>
    </>
  );
}

/**
 * 실시간 가격 진단.
 *
 * 왜 필요한가: 브라우저가 시세 소스를 직접 부르는 경로는 **소스가 CORS 를
 * 허용하는지**에 달려 있는데, 이건 사용자의 브라우저·네트워크·시점에 따라
 * 달라집니다. 개발 환경에서 확인한 결과가 사용자 환경에서 같으리라는 보장이
 * 없습니다.
 *
 * 그래서 추측하게 두지 않고 **직접 눌러 확인**하게 합니다. 실패하면 무엇을
 * 하면 되는지(백엔드 연결)까지 같은 자리에서 알려줍니다.
 */
function QuoteDiagnostic() {
  const backend = useBackend();
  const [result, setResult] = useState<
    | { ok: true; text: string }
    | { ok: false; text: string }
    | null
  >(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setResult(null);
    try {
      if (backend.mode === "live") {
        const [q] = await api.quotes("US", ["AAPL"]);
        setResult(
          q?.source === "live"
            ? { ok: true, text: `백엔드가 AAPL 현재가 ${q.price} 를 받아왔습니다.` }
            : {
                ok: false,
                text:
                  `백엔드는 연결되었지만 시세 소스에서 값을 받지 못했습니다. ` +
                  `${q?.note ?? ""} 저장된 종가로 표시됩니다.`,
              },
        );
      } else {
        const quotes = await fetchBrowserQuotes("US", ["AAPL"]);
        const q = quotes.get("AAPL");
        setResult(
          q
            ? {
                ok: true,
                text: `브라우저가 직접 AAPL 현재가 ${q.price} 를 받아왔습니다 (${q.source}). 1분마다 갱신됩니다.`,
              }
            : { ok: false, text: "응답은 왔지만 가격이 비어 있습니다." },
        );
      }
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3>실시간 가격 진단</h3>
      <div className="card">
        <div className="row">
          <button onClick={check} disabled={busy}>
            {busy ? "확인 중…" : "지금 확인"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            AAPL 로 한 번 조회해 봅니다
          </span>
        </div>

        {result && (
          <div
            className={`banner ${result.ok ? "info" : "warn"}`}
            style={{ marginTop: 10 }}
          >
            <strong>{result.ok ? "가격 갱신이 동작합니다" : "가격 갱신이 막혀 있습니다"}</strong>
            {result.text}
            {!result.ok && (
              <div style={{ marginTop: 6 }}>
                위의 <strong>백엔드 연결</strong>에 주소를 넣으면 서버가 대신
                조회하므로 이 제약을 받지 않습니다.
              </div>
            )}
          </div>
        )}

        <div className="caveat" style={{ marginTop: 10 }}>
          백엔드 없이 쓰는 브라우저 직접 조회는 비공식 무료 엔드포인트에
          의존합니다. 소스가 CORS 를 막거나 규격을 바꾸면 실패하며, 그건 환경마다
          다를 수 있어 <strong>여기서 직접 확인하는 것이 유일하게 확실한
          방법</strong>입니다.
        </div>
        <div className="caveat">
          성공해도 <strong>실시간이 아닙니다.</strong> 무료 소스는 통상 15분 이상
          지연되며, 갱신 주기를 줄여도 이 지연은 줄지 않습니다.
        </div>
      </div>
    </>
  );
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
      <h3>인증정보</h3>

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
