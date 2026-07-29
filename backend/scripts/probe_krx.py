#!/usr/bin/env python
"""Phase 0 스파이크: KRX 데이터 접근 판정.

계획의 갈림길입니다. 이 스크립트가 답해야 하는 질문은 하나입니다:

    "KRX Open API 로 **종목별 연기금 순매수**를 받을 수 있는가?"

  - 받을 수 있으면  -> 단일 소스로 진행
  - 받을 수 없으면  -> 시세는 Open API, 수급은 KRX 정보데이터시스템(MDC) 으로 분리

사용법:
    export KRX_AUTH_KEY=...            # 또는 backend/.env
    uv run python scripts/probe_krx.py            # Open API 엔드포인트 판정
    uv run python scripts/probe_krx.py --mdc      # MDC 수급 경로 + 투자자코드 검증
    uv run python scripts/probe_krx.py --all

출력은 `backend/data/probe/` 에 엔드포인트별 응답 스키마로 저장됩니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.providers.base import NotSubscribed, ProviderError  # noqa: E402
from app.providers.krx_mdc import (  # noqa: E402
    BLD_NET_BUY_TOP,
    INVESTOR_CODES,
    KrxMdcFlowProvider,
)
from app.providers.krx_openapi import ENDPOINTS, KrxOpenApiClient  # noqa: E402

OUT_DIR = settings.data_dir / "probe"

# 수급 판정의 핵심: 응답에 이런 필드가 있으면 투자자별 수급 데이터입니다.
FLOW_FIELD_HINTS = ("INVST", "NETBID", "TRDVAL_NET", "연기금", "PSNL", "FORN")


def recent_business_day(back: int = 3) -> date:
    """최근 영업일 근사치. 주말만 피하면 프로브 목적에는 충분합니다."""
    d = date.today() - timedelta(days=back)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"row_count": 0, "fields": []}
    return {
        "row_count": len(rows),
        "fields": sorted(rows[0].keys()),
        "sample": rows[0],
    }


def probe_openapi(trade_date: date) -> dict:
    print(f"\n=== KRX Open API 프로브 (기준일 {trade_date}) ===")
    results: dict[str, dict] = {}
    flow_capable: list[str] = []
    not_subscribed: list[str] = []

    with KrxOpenApiClient() as client:
        print(f"남은 호출 예산: {client.budget.remaining}")
        for ep in ENDPOINTS:
            tag = "확인됨" if ep.confirmed else "추정  "
            try:
                payload = client.call(
                    ep, {"basDd": trade_date.strftime("%Y%m%d")}, use_cache=False
                )
                rows = KrxOpenApiClient.rows_of(payload)
                info = summarize(rows)
                info["status"] = "ok"
                fields = " ".join(info["fields"])
                if any(h in fields.upper() or h in fields for h in FLOW_FIELD_HINTS):
                    info["flow_fields_detected"] = True
                    flow_capable.append(ep.name)
                print(f"  [{tag}] {ep.name:24s} OK        rows={info['row_count']}")
            except NotSubscribed as exc:
                # '없음' 이 아니라 '아직 신청 안 함'. 이 구분이 판정을 좌우합니다.
                info = {"status": "not_subscribed", "error": str(exc)}
                not_subscribed.append(ep.name)
                print(f"  [{tag}] {ep.name:24s} 미신청/없음")
            except ProviderError as exc:
                info = {"status": "error", "error": str(exc)}
                print(f"  [{tag}] {ep.name:24s} FAIL      {exc}")
            results[ep.name] = {"label": ep.label, "confirmed": ep.confirmed, **info}

    print("\n--- 판정 ---")
    if flow_capable:
        print(f"  투자자별 수급 엔드포인트 발견: {flow_capable}")
        print("  => 단일 소스(KRX Open API)로 진행 가능")
    elif not_subscribed:
        print("  투자자별 수급 엔드포인트를 확인하지 못했습니다.")
        print("  단, 아래 엔드포인트가 '미신청/없음' 으로 나왔습니다:")
        for name in not_subscribed:
            print(f"    - {name}")
        print("\n  ** 판정 보류 **")
        print("  KRX 는 인증키 발급과 서비스별 '이용신청' 이 별개입니다.")
        print("  openapi.krx.co.kr > 서비스 이용 > 주식 에서 목록을 직접 확인하고,")
        print("  투자자별 매매동향 계열 서비스가 있으면 이용신청 후 재실행하십시오.")
        print("  목록에 아예 없다면 그때 MDC 경로로 확정합니다 (--mdc).")
    else:
        print("  투자자별 수급 엔드포인트 없음")
        print("  => 시세는 Open API, 종목별 연기금 수급은 MDC 경로로 분리 (--mdc 실행)")

    results["_verdict"] = {
        "flow_capable_endpoints": flow_capable,
        "not_subscribed": not_subscribed,
        # 미신청 항목이 남아 있으면 '없다' 고 결론지을 수 없습니다.
        "single_source_possible": bool(flow_capable),
        "verdict_final": bool(flow_capable) or not not_subscribed,
    }
    return results


def probe_mdc(trade_date: date) -> dict:
    """MDC 수급 경로 + 투자자 구분 코드 검증.

    투자자 코드가 틀리면 조용히 엉뚱한 구분의 데이터를 가져오게 되므로, 코드별로
    실제 응답을 받아 서로 다른 값이 나오는지 확인합니다.
    """
    print(f"\n=== KRX MDC 수급 프로브 (기준일 {trade_date}) ===")
    dd = trade_date.strftime("%Y%m%d")
    results: dict[str, dict] = {}

    with KrxMdcFlowProvider() as provider:
        for label, code in INVESTOR_CODES.items():
            try:
                rows = provider._post(
                    {
                        "bld": BLD_NET_BUY_TOP,
                        "mktId": "STK",
                        "invstTpCd": code,
                        "strtDd": dd,
                        "endDd": dd,
                        "money": "1",
                        "csvxls_isNo": "false",
                    }
                )
                info = summarize(rows)
                info["status"] = "ok"
                print(f"  {label:8s} (code {code}) rows={info['row_count']}")
                if rows:
                    print(f"           필드: {', '.join(info['fields'][:8])}")
            except ProviderError as exc:
                info = {"status": "error", "error": str(exc)}
                print(f"  {label:8s} (code {code}) FAIL {exc}")
            results[label] = {"code": code, **info}

        # 코드 검증: 서로 다른 투자자 구분이 동일한 데이터를 반환하면 코드가 틀린 것
        samples = {
            k: json.dumps(v.get("sample"), sort_keys=True, ensure_ascii=False)
            for k, v in results.items()
            if v.get("sample")
        }
        distinct = len(set(samples.values()))
        print("\n--- 판정 ---")
        if len(samples) >= 2 and distinct == 1:
            print("  경고: 모든 투자자 구분이 동일한 응답. invstTpCd 코드가 틀렸습니다.")
            results["_verdict"] = {"codes_valid": False}
        elif samples:
            print(f"  투자자 구분 {len(samples)}개가 서로 다른 응답 반환 -- 코드 유효")
            results["_verdict"] = {"codes_valid": True}
        else:
            print("  응답 없음 -- 휴장일이거나 엔드포인트 규격이 변경되었을 수 있습니다.")
            results["_verdict"] = {"codes_valid": None}

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="KRX 데이터 접근 판정 (Phase 0)")
    parser.add_argument("--mdc", action="store_true", help="MDC 수급 경로만 프로브")
    parser.add_argument("--all", action="store_true", help="둘 다 프로브")
    parser.add_argument("--date", type=str, default=None, help="기준일 YYYYMMDD")
    args = parser.parse_args()

    trade_date = (
        date.fromisoformat(
            f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
        )
        if args.date
        else recent_business_day()
    )

    settings.ensure_dirs()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}

    run_openapi = args.all or not args.mdc
    run_mdc = args.all or args.mdc

    if run_openapi:
        try:
            report["openapi"] = probe_openapi(trade_date)
        except ProviderError as exc:
            print(f"\nOpen API 프로브 중단: {exc}")
            report["openapi"] = {"_verdict": {"error": str(exc)}}

    if run_mdc:
        report["mdc"] = probe_mdc(trade_date)

    out = OUT_DIR / f"probe_{trade_date:%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
