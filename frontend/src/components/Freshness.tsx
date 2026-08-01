"use client";

import { pct } from "@/lib/api";
import { useRelativeTime } from "@/lib/useBackend";
import { isMoving, ORIGIN_LABEL, type ResolvedQuote } from "@/lib/useQuotes";

/**
 * "이 화면의 숫자가 언제 것인가" 한 줄.
 *
 * 모든 데이터 화면에 붙입니다. 갱신 시각을 적지 않으면 사용자는 화면을 연
 * 순간의 값을 계속 보면서도 그것이 낡았다는 사실을 알 수 없습니다.
 */
export function Freshness({
  updatedAt,
  onRefresh,
  loading,
  intervalMs,
  asOf,
  extra,
}: {
  updatedAt: number | null;
  onRefresh?: () => void;
  loading?: boolean;
  intervalMs?: number;
  /** 데이터 자체의 기준일 (갱신 시각과 다릅니다 -- 이걸 섞으면 안 됩니다). */
  asOf?: string | null;
  extra?: React.ReactNode;
}) {
  const ago = useRelativeTime(updatedAt);
  return (
    <div className="freshness">
      {asOf && <span>기준일 {asOf}</span>}
      <span>{ago} 불러옴</span>
      {intervalMs ? <span>· {Math.round(intervalMs / 1000)}초마다 자동</span> : null}
      {onRefresh && (
        <button className="ghost tiny" onClick={onRefresh} disabled={loading}>
          {loading ? "…" : "지금 갱신"}
        </button>
      )}
      {extra}
    </div>
  );
}

/**
 * 표 안의 가격 칸.
 *
 * 값이 움직이는 것인지(지연 시세) 굳은 것인지(스냅샷 종가)를 배지로 구분합니다.
 * 표에 숫자만 있으면 전부 같은 신뢰도로 읽히는데, 실제로는 옆 칸끼리 출처가
 * 다를 수 있습니다 -- 어떤 종목은 조회에 성공하고 어떤 종목은 실패하기
 * 때문입니다.
 */
export function QuoteCell({
  quote,
  fallback,
}: {
  quote: ResolvedQuote | undefined;
  fallback: number | null;
}) {
  if (!quote) {
    return (
      <span className="muted">
        {fallback === null
          ? "—"
          : fallback.toLocaleString(undefined, { maximumFractionDigits: 2 })}
      </span>
    );
  }

  const moving = isMoving(quote.origin);
  const tone =
    quote.changePct === null
      ? ""
      : quote.changePct > 0
        ? "pos"
        : quote.changePct < 0
          ? "neg"
          : "";

  return (
    <span title={quote.note} style={{ whiteSpace: "nowrap" }}>
      {quote.price === null
        ? "—"
        : quote.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
      {quote.changePct !== null && (
        <span className={tone} style={{ marginLeft: 6, fontSize: 12 }}>
          {pct(quote.changePct, 1)}
        </span>
      )}
      {!moving && (
        <span className="badge stale" style={{ marginLeft: 6 }}>
          {ORIGIN_LABEL[quote.origin]}
        </span>
      )}
    </span>
  );
}
