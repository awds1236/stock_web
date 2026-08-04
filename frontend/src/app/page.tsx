"use client";

import { api, type Coverage } from "@/lib/api";
import { HAS_SNAPSHOTS } from "@/lib/backend";
import { boardLabel } from "@/lib/sectorNames";
import { useBackend, usePolling, useRelativeTime } from "@/lib/useBackend";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

/** 데이터 현황 갱신 주기. 수집이 도는 동안 화면이 따라가야 합니다. */
const COVERAGE_POLL_MS = 30_000;
/** 스냅샷 모드에서 새 배포를 감지하는 주기. */
const BUILD_POLL_MS = 60_000;

export default function Home() {
  const backend = useBackend();
  const live = backend.mode === "live";

  const [coverage, setCoverage] = useState<Coverage[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [newBuild, setNewBuild] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [message, setMessage] = useState<{
    tone: "info" | "warn" | "bad";
    title: string;
    body: string;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      setCoverage(await api.coverage());
      setUpdatedAt(Date.now());
      setMessage(null);
    } catch (e) {
      setMessage({
        tone: "bad",
        title: live
          ? "백엔드에서 데이터 현황을 읽지 못했습니다"
          : "데이터 스냅샷을 불러올 수 없습니다",
        body: String(e instanceof Error ? e.message : e),
      });
    }
  }, [live]);

  useEffect(() => {
    load();
  }, [load]);

  usePolling(load, COVERAGE_POLL_MS, [load]);

  /**
   * 스냅샷 모드에서 **새 배포를 감지**합니다.
   *
   * 정적 사이트라도 탭을 열어둔 채 CI 가 새 데이터를 배포할 수 있습니다.
   * 그때 화면이 아무 말도 하지 않으면, 사용자는 새로고침해야 한다는 사실을
   * 알 방법이 없습니다 -- 배포된 페이지가 멈춰 보이는 이유 중 하나입니다.
   */
  const checkBuild = useCallback(async () => {
    if (!HAS_SNAPSHOTS) return;
    try {
      const b = await api.buildInfo();
      setGeneratedAt((prev) => {
        if (prev && prev !== b.generated_at) setNewBuild(true);
        return b.generated_at;
      });
    } catch {
      /* 스냅샷 정보가 없어도 치명적이지 않습니다 */
    }
  }, []);

  useEffect(() => {
    checkBuild();
  }, [checkBuild]);

  usePolling(checkBuild, HAS_SNAPSHOTS ? BUILD_POLL_MS : 0, [checkBuild]);

  const ago = useRelativeTime(updatedAt);

  async function runIngest(market: string) {
    setBusy(market);
    setMessage(null);
    try {
      const r = await api.ingest(market, 10);
      setMessage({
        tone: r.rows > 0 ? "info" : "warn",
        title:
          r.rows > 0
            ? `${market} 수집 완료 — ${r.rows.toLocaleString()}행 / ${r.tickers}종목`
            : `${market} 수집된 데이터가 없습니다`,
        body: r.warnings.join(" "),
      });
      await load();
    } catch (e) {
      setMessage({
        tone: "warn",
        title: `${market} 수집 실패`,
        body: String(e instanceof Error ? e.message : e),
      });
    } finally {
      setBusy(null);
    }
  }

  const us = coverage?.find((c) => c.market === "US");
  const kr = coverage?.find((c) => c.market === "KR");

  return (
    <>
      <h2>개요</h2>
      <p className="sub">
        지표를 해석하고 보정된 확률로 예측합니다. 점 예측(&quot;내일 얼마&quot;)은
        하지 않습니다.
      </p>

      <div className="freshness">
        <span>데이터 현황 {ago} 갱신</span>
        <button className="ghost tiny" onClick={load}>
          지금 갱신
        </button>
        {live && <span>· {COVERAGE_POLL_MS / 1000}초마다 자동</span>}
      </div>

      {newBuild && (
        <div className="banner info">
          <strong>새 스냅샷이 배포되었습니다</strong>
          이 탭은 이전 배포의 데이터를 보고 있습니다.{" "}
          <button className="tiny" onClick={() => window.location.reload()}>
            새로고침
          </button>
        </div>
      )}

      {message && (
        <div className={`banner ${message.tone}`}>
          <strong>{message.title}</strong>
          {message.body}
        </div>
      )}

      {!live && HAS_SNAPSHOTS && (
        <div className="banner info">
          <strong>지금은 스냅샷을 보고 있습니다</strong>
          분석·예측 수치는 GitHub Actions 가 만든 시점의 값입니다
          {generatedAt &&
            ` (${generatedAt.slice(0, 16).replace("T", " ")} UTC 생성)`}
          . 가격은 종목 화면에서 브라우저가 직접 갱신합니다. 수집·설정·AI 분석까지
          쓰려면 백엔드를 연결하십시오 — <Link href="/settings">설정 →</Link>
        </div>
      )}

      {live && us && !us.ready && (
        <div className="banner info">
          <strong>여기서 시작하세요 — 미국 주식은 인증키가 필요 없습니다</strong>
          아래 &quot;미국 데이터 수집&quot;을 누르면 바로 분석을 시작할 수
          있습니다. 한국 주식은 KRX 인증키가 필요합니다.
        </div>
      )}

      <h3>데이터 현황</h3>
      <div className="grid grid-2">
        {coverage?.map((c) => (
          <div className="card" key={c.market}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>
                {c.market === "US" ? "미국 (NYSE/NASDAQ)" : "한국 (KOSPI/KOSDAQ)"}
              </strong>
              <span className={c.ready ? "pos" : "muted"}>
                {c.ready ? "준비됨" : "데이터 없음"}
              </span>
            </div>
            <table style={{ marginTop: 10 }}>
              <tbody>
                <tr>
                  <td className="muted">종목 수</td>
                  <td className="num">
                    {c.n_tickers.toLocaleString()}
                    {/* "300종목"만 보여주면 코스닥이 몇 개인지 알 수 없습니다. */}
                    {Object.keys(c.boards ?? {}).length > 0 && (
                      <div className="muted" style={{ fontSize: 11 }}>
                        {Object.entries(c.boards)
                          .map(([b, n]) => `${boardLabel(b)} ${n}`)
                          .join(" · ")}
                      </div>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="muted">거래일 수</td>
                  <td className="num">{c.n_days.toLocaleString()}</td>
                </tr>
                <tr>
                  <td className="muted">데이터 행</td>
                  <td className="num">{c.n_rows.toLocaleString()}</td>
                </tr>
                <tr>
                  <td className="muted">기간</td>
                  <td className="num">
                    {c.first_date
                      ? `${c.first_date.slice(0, 10)} ~ ${c.last_date?.slice(0, 10)}`
                      : "—"}
                  </td>
                </tr>
              </tbody>
            </table>

            {/* 히스토리가 짧아 못 쓰는 기능이 있으면 **미리** 말합니다.
                빈 화면만 보면 사용자는 앱이 고장난 줄 압니다. */}
            {c.latency_note && (
              <div className="caveat">{c.latency_note}</div>
            )}
            {c.history_note && (
              <div className="banner warn" style={{ marginTop: 10 }}>
                <strong>히스토리가 짧습니다</strong>
                {c.history_note}
              </div>
            )}

            {!live ? (
              <div className="caveat" style={{ marginTop: 12 }}>
                {c.ready
                  ? "GitHub Actions 가 주기적으로 갱신합니다. 백엔드를 연결하면 직접 수집할 수 있습니다."
                  : c.market === "KR"
                    ? "한국 데이터는 저장소 Secrets 에 KRX_AUTH_KEY 를 추가하면 다음 배포부터 포함됩니다."
                    : "다음 배포에서 갱신됩니다."}
              </div>
            ) : c.needs_credential ? (
              <div style={{ marginTop: 12 }}>
                <div className="caveat">
                  이 시장은 <code>{c.needs_credential}</code> 인증키가 필요합니다.
                </div>
                <Link
                  href="/settings"
                  className="btn"
                  style={{ marginTop: 8, display: "inline-block" }}
                >
                  설정에서 인증키 입력
                </Link>
              </div>
            ) : (
              <button
                style={{ marginTop: 12 }}
                disabled={busy !== null}
                onClick={() => runIngest(c.market)}
              >
                {busy === c.market
                  ? "수집 중… (수 분 소요)"
                  : `${c.market === "US" ? "미국" : "한국"} 데이터 수집`}
              </button>
            )}
          </div>
        ))}
      </div>

      {(us?.ready || kr?.ready) && (
        <>
          <h3>다음 단계</h3>
          <div className="grid grid-2">
            <Link href="/market" className="card">
              <strong>시장 분석</strong>
              <div className="muted">
                시장 전체 상태와 규칙에 걸린 주목 종목
              </div>
            </Link>
            <Link href="/stocks" className="card">
              <strong>종목 분석</strong>
              <div className="muted">
                현재가 자동 갱신 + 종목 단위 분석
              </div>
            </Link>
            <Link href="/sectors" className="card">
              <strong>섹터 분석</strong>
              <div className="muted">
                문헌상 섹터 단위 예측력이 개별종목과 동등하거나 더 높습니다
              </div>
            </Link>
            <Link href="/forecast" className="card">
              <strong>예측</strong>
              <div className="muted">보정된 확률과 그 확률의 신뢰도 곡선</div>
            </Link>
            <Link href="/ai-logs" className="card">
              <strong>AI 분석 기록</strong>
              <div className="muted">
                실행한 AI 서술과 그때 넘긴 숫자를 함께 보관
              </div>
            </Link>
            <Link href="/methodology" className="card">
              <strong>방법론</strong>
              <div className="muted">예측 성능의 현실적 상한과 근거 논문</div>
            </Link>
          </div>
        </>
      )}

      <h3>이 앱이 하지 않는 것</h3>
      <div className="card">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            <strong>점 예측을 하지 않습니다.</strong> &quot;내일 75,000원&quot;
            같은 출력은 문헌이 뒷받침하는 정확도를 크게 넘어섭니다.
          </li>
          <li>
            <strong>검증되지 않은 신호를 보여주지 않습니다.</strong> 모든 예측은
            아웃오브샘플 품질과 함께 표시됩니다.
          </li>
          <li>
            <strong>낡은 값을 지금 값처럼 보여주지 않습니다.</strong> 화면의 모든
            숫자에 출처와 갱신 시각이 붙습니다.
          </li>
        </ul>
      </div>
    </>
  );
}
