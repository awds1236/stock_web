#!/usr/bin/env python
"""정적 사이트용 데이터 스냅샷 내보내기 (GitHub Pages).

GitHub Pages 는 정적 파일만 서빙하므로 FastAPI 백엔드가 거기서 돌 수 없습니다.
대신 이 스크립트가 CI(GitHub Actions)에서 주기적으로 실행되어:

    1. 데이터를 수집하고 (미국은 키 불필요, 한국은 KRX_AUTH_KEY 환경변수 필요)
    2. API 응답을 JSON 파일로 고정합니다

프론트엔드는 정적 모드(NEXT_PUBLIC_STATIC=1)에서 /api/* 대신 이 파일들을
읽습니다.

구현 원칙: **API 라우터를 TestClient 로 직접 재사용**해 응답을 덤프합니다.
정적 경로와 동적 경로가 같은 코드를 타므로, 둘이 조용히 갈라지는 일이 없습니다
-- 예측 품질 필수 포함 같은 계약도 자동으로 유지됩니다.

사용법:
    python scripts/export_static.py --out ../frontend/public/data
    python scripts/export_static.py --out ... --skip-ingest   # 기존 DB 로만 덤프
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_SLUG_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _slug(name: str) -> str:
    """업종명을 파일명으로.

    **퍼센트 인코딩을 쓰면 안 됩니다.** 한때 `quote(name)` 을 썼는데, 그러면
    '건설' 이 `%EA%B1%B4%EC%84%A4` 라는 이름의 파일이 됩니다. 브라우저가 그
    URL 을 요청하면 웹서버가 퍼센트 인코딩을 **디코딩해서** '건설' 이라는
    파일을 찾으므로, 정작 디스크에 있는 `%EA%B1%B4...` 파일은 영원히 404 가
    납니다. 이중 인코딩으로만 열리는데 브라우저는 그렇게 요청하지 않습니다.

    실제로 이것 때문에 정적 배포에서 **모든 업종의 분석 버튼이 실패**했습니다
    -- 한국은 한글이라서, 미국은 이름에 공백이 있어서(`%20`).

    그래서 UTF-8 바이트 단위로 ASCII 만 남기고 나머지는 `_xx` 로 적습니다.
    결과가 순수 ASCII 라 URL 디코딩이 항등이고, 어떤 호스트에서도 같습니다.
    유니코드 파일명을 그대로 쓰는 방법도 있지만 정규화(NFC/NFD) 차이로
    조용히 깨질 수 있어 피했습니다.

    프론트엔드에 **같은 규칙**이 있습니다(lib/api.ts `sectorSlug`).
    두 구현이 일치하는지는 `tests/test_sector_slug.py` 가 실제로 대조합니다.
    """
    out: list[str] = []
    for byte in name.encode("utf-8"):
        ch = chr(byte)
        out.append(ch if ch in _SLUG_SAFE else f"_{byte:02x}")
    return "".join(out)

FORECAST_TARGETS = ("direction", "return", "volatility")
# 정적 배포에는 21일(1개월) 예측만 포함합니다. 타깃×기간 전 조합을 생성하면
# 워크포워드가 조합 수만큼 돌아 CI 시간이 배로 늘고, 21일이 문헌 기준
# (월간 R²) 과도 맞습니다.
FORECAST_HORIZON = 21


def main() -> int:
    ap = argparse.ArgumentParser(description="GitHub Pages 용 데이터 스냅샷")
    ap.add_argument("--out", required=True, help="JSON 출력 디렉터리")
    ap.add_argument("--years", type=int, default=10, help="수집 기간(년)")
    ap.add_argument(
        "--skip-ingest",
        action="store_true",
        help="수집 없이 기존 DB 로만 덤프 (로컬 테스트용)",
    )
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. 수집 ──────────────────────────────────────────────────────────
    if not args.skip_ingest:
        from app.ingest.pipeline import ingest_kr_prices, ingest_us_prices

        us = ingest_us_prices(years=args.years)
        print(f"US 수집: {us.rows}행 / {us.tickers}종목 ({us.start} ~ {us.end})")
        for w in us.warnings:
            print(f"  경고: {w}")
        if us.rows == 0:
            print("US 데이터 수집 실패 -- 배포를 중단합니다.", file=sys.stderr)
            return 1

        # 한국은 KRX 키가 있을 때만. Actions 에서는 repo secret 으로 주입됩니다.
        if os.getenv("KRX_AUTH_KEY", "").strip():
            try:
                kr = ingest_kr_prices(years=args.years)
                print(f"KR 수집: {kr.rows}행 / {kr.tickers}종목")
                # 실패 원인을 반드시 로그에 남깁니다. 실제 배포에서 'KR 0행'만
                # 찍히고 이유가 보이지 않아 진단이 불가능했던 사례가 있습니다 --
                # 경고를 모아두고 출력하지 않으면 없는 것과 같습니다.
                for w in kr.warnings[:3]:
                    print(f"  KR 원인: {w}")
                if len(kr.warnings) > 3:
                    print(f"  … 외 {len(kr.warnings) - 3}건 (전부 유사한 오류)")
                if kr.rows == 0:
                    print(
                        "  KR 진단 안내: 위 원인이 '이용신청'을 언급하면 "
                        "openapi.krx.co.kr 에서 유가증권/코스닥 일별매매정보의 "
                        "서비스 이용신청이 필요합니다. 연결 오류라면 KRX 가 "
                        "해외(GitHub 러너) IP 를 차단했을 가능성이 있습니다."
                    )
            except Exception as exc:  # noqa: BLE001 -- KR 실패가 US 배포를 막으면 안 됩니다
                print(f"KR 수집 실패 (US 만 배포): {exc}")
        else:
            print("KRX_AUTH_KEY 없음 -- 미국만 배포합니다.")

    # ── 2. API 응답 덤프 ─────────────────────────────────────────────────
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    failures: list[str] = []

    def dump(path: str, name: str, *, required: bool) -> Any | None:
        resp = client.get(path)
        if resp.status_code != 200:
            msg = f"{path} -> HTTP {resp.status_code}: {resp.text[:150]}"
            if required:
                failures.append(msg)
                print(f"실패: {msg}", file=sys.stderr)
            else:
                print(f"건너뜀: {msg}")
            return None
        body = resp.json()
        (out / name).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return body

    dump("/api/markets", "markets.json", required=True)
    dump("/api/coverage", "coverage.json", required=True)

    for market in ("US", "KR"):
        required = market == "US"
        universe = dump(f"/api/universe/{market}", f"universe-{market}.json",
                        required=required)
        if not universe:
            continue

        for item in universe:
            t = item["ticker"]
            dump(f"/api/stocks/{market}/{t}", f"stocks-{market}-{t}.json",
                 required=required)
            # 종목별 분석도 함께 고정합니다. 정적 배포에서 "분석" 버튼이
            # 로컬에서만 되는 기능이면, 두 배포가 서로 다른 앱이 됩니다.
            dump(f"/api/analyze/stock/{market}/{t}",
                 f"analysis-stock-{market}-{t}.json", required=False)

        dump(f"/api/sectors/{market}", f"sectors-{market}.json", required=False)
        sectors = dump(
            f"/api/sectors/{market}?level=industry",
            f"sectors-{market}-industry.json",
            required=False,
        )
        dump(f"/api/watchlist/{market}", f"watchlist-{market}.json", required=False)
        dump(f"/api/analyze/market/{market}", f"analysis-market-{market}.json",
             required=False)
        # 섹터 주도권·순환. 정적 배포에서도 AI 키 없이 숫자가 전부 보여야 합니다.
        dump(f"/api/leadership/{market}", f"leadership-{market}.json", required=False)
        for row in sectors or []:
            name = row["sector"]
            dump(
                f"/api/analyze/sector/{market}?sector={quote(name)}&level=industry",
                f"analysis-sector-{market}-{_slug(name)}.json",
                required=False,
            )

        for target in FORECAST_TARGETS:
            print(f"예측 계산 중: {market}/{target} ({FORECAST_HORIZON}일)…")
            dump(
                f"/api/forecast/{market}?target={target}"
                f"&horizon_days={FORECAST_HORIZON}",
                f"forecast-{market}-{target}-{FORECAST_HORIZON}.json",
                required=required,
            )

    (out / "build-info.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "forecast_horizon_days": FORECAST_HORIZON,
                "note": "GitHub Actions 가 생성한 정적 스냅샷입니다.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    n_files = len(list(out.glob("*.json")))
    print(f"\n완료: {n_files}개 파일 -> {out}")
    if failures:
        print(f"\n필수 항목 {len(failures)}건 실패 -- 배포를 중단합니다.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
