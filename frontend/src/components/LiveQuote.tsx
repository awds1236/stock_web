"use client";

import { pct } from "@/lib/api";
import { useRelativeTime } from "@/lib/useBackend";
import {
  DEFAULT_QUOTE_INTERVAL_MS,
  isMoving,
  ORIGIN_LABEL,
  useQuotes,
} from "@/lib/useQuotes";
import { useMemo, useState } from "react";

/**
 * 갱신 주기 선택지.
 *
 * **1분보다 짧은 선택지는 두지 않습니다.** 예전에는 15초·30초가 있었는데,
 * 원천이 15분 이상 지연된 시세라 값은 그대로면서 요청만 4배가 됩니다. 무료
 * 소스가 차단으로 응답하게 만드는 것은 대개 그 빈도입니다.
 */
const INTERVALS = [
  { ms: 60_000, label: "1분" },
  { ms: 300_000, label: "5분" },
  { ms: 600_000, label: "10분" },
];

/**
 * 종목 상세 상단의 현재가.
 *
 * **실시간이 아니라는 것을 화면에서 숨기지 않습니다.** 미국·한국 모두 무료
 * 소스의 지연 시세이며(15분 이상), 체결 가격이 아닙니다. 숫자만 크게 띄우면
 * 사용자는 체결 가격으로 읽습니다 -- 그래서 출처 배지와 설명을 항상 함께 둡니다.
 *
 * 값이 시간에 따라 움직이지 않는 경우(스냅샷 종가)에는 **갱신 표시를 아예
 * 다르게** 합니다. "10초 전 갱신"이라고 쓰면 그 값이 10초 전 시세인 것처럼
 * 읽히는데, 실제로는 며칠 전 종가일 수 있습니다.
 */
export function LiveQuote({
  market,
  ticker,
  /** 상장 시장 (KOSPI/KOSDAQ). 한국 종목의 시세 심볼 접미사를 고릅니다. */
  board,
  /** 스냅샷에 있는 마지막 종가. 시세 조회가 막혀도 가격 칸이 비지 않게 합니다. */
  fallbackClose,
}: {
  market: string;
  ticker: string;
  board?: string | null;
  fallbackClose?: number | null;
}) {
  const [intervalMs, setIntervalMs] = useState(DEFAULT_QUOTE_INTERVAL_MS);
  const requests = useMemo(() => [{ ticker, board: board ?? null }], [ticker, board]);
  const fallbackCloses = useMemo(
    () => new Map([[ticker, fallbackClose ?? null]]),
    [ticker, fallbackClose],
  );
  const { quotes, updatedAt, error, loading, refresh, cooldownMs } = useQuotes(
    market,
    requests,
    { intervalMs, fallbackCloses },
  );
  const ago = useRelativeTime(updatedAt);
  const quote = quotes.get(ticker);

  if (!quote) {
    return (
      <div className="card">
        <span className="muted">
          {loading ? "현재가를 불러오는 중…" : `현재가를 가져오지 못했습니다. ${error ?? ""}`}
        </span>
      </div>
    );
  }

  const moving = isMoving(quote.origin);
  const tone =
    quote.change === null ? "" : quote.change > 0 ? "pos" : quote.change < 0 ? "neg" : "";

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 14 }}>
          <span
            style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}
          >
            {quote.price === null
              ? "—"
              : quote.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          {quote.change !== null && (
            <span className={tone} style={{ fontSize: 14 }}>
              {`${quote.change > 0 ? "+" : ""}${quote.change.toLocaleString(undefined, {
                maximumFractionDigits: 2,
              })} (${pct(quote.changePct)})`}
            </span>
          )}
          <span className="muted" style={{ fontSize: 11 }}>
            {quote.currency ?? ""}
          </span>
        </div>

        <div className="row" style={{ gap: 8 }}>
          <span className={`badge ${moving ? "" : "stale"}`}>
            {ORIGIN_LABEL[quote.origin]}
          </span>
          {moving ? (
            <>
              <span className="muted" style={{ fontSize: 11 }}>
                {ago} 갱신
              </span>
              <select
                value={intervalMs}
                onChange={(e) => setIntervalMs(Number(e.target.value))}
                style={{ height: 24, fontSize: 11, padding: "0 4px" }}
                title="현재가 갱신 주기"
              >
                {INTERVALS.map((i) => (
                  <option key={i.ms} value={i.ms}>
                    {i.label}마다
                  </option>
                ))}
              </select>
            </>
          ) : (
            <span className="muted" style={{ fontSize: 11 }}>
              움직이지 않는 값
            </span>
          )}
          {/* 쿨다운 중에는 눌러도 네트워크로 나가지 않습니다(캐시가 돌아옴).
              버튼을 그대로 두면 "눌렀는데 안 바뀐다"로 읽히므로, 언제 다시
              부를 수 있는지 버튼에 적습니다. */}
          <button
            className="ghost tiny"
            onClick={refresh}
            disabled={loading || cooldownMs > 0}
            title={
              cooldownMs > 0
                ? "같은 종목은 1분에 한 번만 조회합니다 (무료 소스 차단 방지)"
                : "지금 다시 조회"
            }
          >
            {loading
              ? "…"
              : cooldownMs > 0
                ? `${Math.ceil(cooldownMs / 1000)}초 후`
                : "새로고침"}
          </button>
        </div>
      </div>
      <div className="caveat">{quote.note}</div>
    </div>
  );
}
