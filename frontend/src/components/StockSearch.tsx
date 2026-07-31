"use client";

import { api, type SearchHit } from "@/lib/api";
import { industryLabel, sectorLabel } from "@/lib/sectorNames";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 종목 검색 자동완성.
 *
 * 300종목짜리 드롭다운은 사실상 쓸 수 없습니다. 몇 글자만 입력하면 관련 종목이
 * 뜨고, 키보드(↑↓/Enter/Esc)로도 고를 수 있게 합니다.
 *
 * 디바운스를 두는 이유: 한 글자마다 요청하면 한글 조합 입력 중에도 요청이
 * 쏟아집니다. 정적 배포에서는 검색이 클라이언트 필터로 동작하므로 지연이
 * 없지만, 로컬 백엔드에서는 실제 HTTP 호출입니다.
 */
export function StockSearch({
  market,
  onSelect,
  selected,
}: {
  market: string;
  onSelect: (ticker: string) => void;
  selected?: string | null;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const run = useCallback(
    async (q: string) => {
      try {
        setHits(await api.search(market, q, 20));
        setError(null);
      } catch (e) {
        setHits([]);
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [market],
  );

  // 시장이 바뀌면 이전 시장의 검색 결과를 지웁니다 -- 남겨두면 없는 종목을
  // 고를 수 있게 되어 404 로 이어집니다.
  useEffect(() => {
    setQuery("");
    setHits([]);
    setOpen(false);
  }, [market]);

  useEffect(() => {
    const t = setTimeout(() => {
      if (open) run(query);
    }, 180);
    return () => clearTimeout(t);
  }, [query, open, run]);

  // 바깥 클릭 시 닫기
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function choose(hit: SearchHit) {
    onSelect(hit.ticker);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && hits[active]) {
      e.preventDefault();
      choose(hits[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={boxRef} style={{ position: "relative", flex: 1, minWidth: 260 }}>
      <input
        value={query}
        placeholder={
          selected
            ? `${selected} — 다른 종목 검색 (코드·이름·업종)`
            : "종목 검색 (코드·이름·업종 일부만 입력)"
        }
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 20,
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            maxHeight: 340,
            overflowY: "auto",
            boxShadow: "0 8px 24px rgba(0,0,0,.25)",
          }}
        >
          {error && (
            <div className="caveat" style={{ padding: 10 }}>
              {error}
            </div>
          )}
          {!error && hits.length === 0 && (
            <div className="caveat" style={{ padding: 10 }}>
              {query
                ? "일치하는 종목이 없습니다."
                : "종목코드·이름·업종 중 무엇이든 일부만 입력하세요."}
            </div>
          )}
          {hits.map((h, i) => (
            <button
              key={h.ticker}
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(h)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                background: i === active ? "var(--panel-2)" : "transparent",
                color: "var(--text)",
                border: "none",
                borderBottom: "1px solid var(--border)",
                padding: "8px 12px",
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 600 }}>
                {h.ticker}
                {h.name ? (
                  <span style={{ fontWeight: 400 }}> {h.name}</span>
                ) : null}
              </div>
              <div className="muted" style={{ fontSize: 11 }}>
                {h.industry
                  ? industryLabel(h.industry)
                  : sectorLabel(h.sector)}
                {h.market_cap ? ` · 시총 ${fmtCap(h.market_cap)}` : ""}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 시가총액을 조/억 단위로 축약. 자릿수를 다 보여주면 목록이 읽히지 않습니다. */
function fmtCap(v: number): string {
  if (v >= 1e12) return `${(v / 1e12).toFixed(1)}조`;
  if (v >= 1e8) return `${(v / 1e8).toFixed(0)}억`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
