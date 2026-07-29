"use client";

import { api, IS_STATIC, type Coverage } from "@/lib/api";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

export default function Home() {
  const [coverage, setCoverage] = useState<Coverage[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [message, setMessage] = useState<{
    tone: "info" | "warn" | "bad";
    title: string;
    body: string;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      setCoverage(await api.coverage());
    } catch (e) {
      setMessage({
        tone: "bad",
        title: IS_STATIC
          ? "데이터 스냅샷을 불러올 수 없습니다"
          : "백엔드에 연결할 수 없습니다",
        body: IS_STATIC
          ? `배포 워크플로가 성공했는지 확인하십시오. (${String(e)})`
          : `backend 가 실행 중인지 확인하십시오: uv run uvicorn app.main:app --port 8000 (${String(e)})`,
      });
    }
    if (IS_STATIC) {
      // 스냅샷 생성 시각 -- 없어도 치명적이지 않으므로 조용히 무시합니다.
      api
        .buildInfo()
        .then((b) => setGeneratedAt(b.generated_at))
        .catch(() => {});
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

      {message && (
        <div className={`banner ${message.tone}`}>
          <strong>{message.title}</strong>
          {message.body}
        </div>
      )}

      {IS_STATIC && (
        <div className="banner info">
          <strong>GitHub Pages 정적 스냅샷</strong>
          이 사이트는 GitHub Actions 가 주기적으로 생성하는 스냅샷입니다.
          {generatedAt &&
            ` 마지막 갱신: ${generatedAt.slice(0, 16).replace("T", " ")} UTC.`}{" "}
          수집·설정 기능은 로컬 실행에서만 동작합니다.
        </div>
      )}

      {!IS_STATIC && us && !us.ready && (
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
                  <td className="num">{c.n_tickers.toLocaleString()}</td>
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

            {IS_STATIC ? (
              <div className="caveat" style={{ marginTop: 12 }}>
                {c.ready
                  ? "GitHub Actions 가 자동 갱신합니다."
                  : c.market === "KR"
                    ? "한국 데이터는 저장소 Secrets 에 KRX_AUTH_KEY 를 추가하면 다음 배포부터 포함됩니다."
                    : "다음 배포에서 갱신됩니다."}
              </div>
            ) : c.needs_credential ? (
              <div style={{ marginTop: 12 }}>
                <div className="caveat">
                  이 시장은 <code>{c.needs_credential}</code> 인증키가 필요합니다.
                </div>
                <Link href="/settings" className="btn" style={{ marginTop: 8, display: "inline-block" }}>
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
            <Link href="/stocks" className="card">
              <strong>종목 분석</strong>
              <div className="muted">
                가격 차트와 지표, 그리고 각 지표의 해석과 한계
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
              <div className="muted">
                보정된 확률과 그 확률의 신뢰도 곡선
              </div>
            </Link>
            <Link href="/methodology" className="card">
              <strong>방법론</strong>
              <div className="muted">
                예측 성능의 현실적 상한과 근거 논문
              </div>
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
            <strong>한국과 미국 시그널을 비교하지 않습니다.</strong> 한국은
            투자자별 수급, 미국은 내부자 매매로 재는 대상이 다릅니다.
          </li>
        </ul>
      </div>
    </>
  );
}
