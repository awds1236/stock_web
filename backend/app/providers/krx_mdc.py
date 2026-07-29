"""KRX 정보데이터시스템(data.krx.co.kr) 수급 provider -- 대체 경로.

Phase 0 스파이크에서 KRX Open API 에 종목별 투자자별 매매동향이 없다고 판정되면
이 provider 를 사용합니다. 정보데이터시스템의 화면(MDCSTAT023 투자자별 거래실적
(개별종목), MDCSTAT024 투자자별 순매수상위종목)이 내부적으로 호출하는 JSON
엔드포인트를 그대로 사용합니다.

정직한 한계 표기:
    이것은 공개 REST 규격이 아니라 웹 화면이 쓰는 내부 엔드포인트입니다. KRX 가
    화면을 개편하면 깨질 수 있고, 공식 지원 대상이 아닙니다. 시세·종목정보는
    가급적 공식 Open API 를 쓰고, 이 경로는 '공식 API 에 없는 수급 데이터' 로만
    한정해 사용합니다.

    아래 bld 경로와 invstTpCd 코드는 `scripts/probe_krx.py --mdc` 로 실제 응답을
    확인한 뒤 확정하십시오. 코드가 틀리면 조용히 다른 투자자 구분의 데이터를
    가져오게 되므로, ingest 단계에서 반드시 검증합니다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pandas as pd

from app.config import settings
from app.providers.base import (
    INVESTOR_FOREIGN,
    INVESTOR_INSTITUTION,
    INVESTOR_PENSION,
    INVESTOR_RETAIL,
    FlowProvider,
    ProviderError,
)
from app.providers.cache import DiskCache

BLD_INVESTOR_BY_STOCK = "dbms/MDC/STAT/standard/MDCSTAT02301"
BLD_NET_BUY_TOP = "dbms/MDC/STAT/standard/MDCSTAT02401"

# KRX 투자자 구분 코드. probe 로 검증 필요.
INVESTOR_CODES: dict[str, str] = {
    INVESTOR_PENSION: "6000",  # 연기금 등
    INVESTOR_INSTITUTION: "7050",  # 기관합계
    INVESTOR_RETAIL: "8000",  # 개인
    INVESTOR_FOREIGN: "9000",  # 외국인
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; stock-web/0.1)",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}


class KrxMdcFlowProvider(FlowProvider):
    def __init__(
        self,
        *,
        cache: DiskCache | None = None,
        timeout: float = 30.0,
        markets: tuple[str, ...] = ("STK", "KSQ"),
    ) -> None:
        self.cache = cache or DiskCache()
        self.markets = markets
        self._client = httpx.Client(timeout=timeout, headers=_HEADERS)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KrxMdcFlowProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, payload: dict[str, str]) -> list[dict[str, Any]]:
        ns = "krx_mdc"
        hit = self.cache.get(ns, payload)
        if hit is not None:
            return hit

        try:
            resp = self._client.post(settings.krx_mdc_base, data=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"KRX MDC 호출 실패: {exc}") from exc
        if resp.status_code != 200:
            raise ProviderError(f"KRX MDC: HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"KRX MDC: JSON 파싱 실패 -- {resp.text[:200]}") from exc

        rows: list[dict[str, Any]] = []
        for value in body.values():
            if isinstance(value, list):
                rows = value
                break

        self.cache.set(ns, payload, rows)
        return rows

    def fetch_daily_flows(self, trade_date: date) -> pd.DataFrame:
        """해당 일자 전 종목 × 투자자 구분 순매수.

        일자별로 투자자 구분마다 1회씩 조회합니다(전 종목 일괄). 종목별로 개별
        호출하면 호출 수가 폭증하므로 반드시 일자 단위 일괄 조회를 씁니다.
        """
        dd = trade_date.strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []

        for market in self.markets:
            for label, code in INVESTOR_CODES.items():
                rows = self._post(
                    {
                        "bld": BLD_NET_BUY_TOP,
                        "mktId": market,
                        "invstTpCd": code,
                        "strtDd": dd,
                        "endDd": dd,
                        "money": "1",  # 1 = 거래대금 기준
                        "csvxls_isNo": "false",
                    }
                )
                if not rows:
                    continue
                df = pd.DataFrame(rows)
                df["investor_type"] = label
                frames.append(df)

        cols = ["date", "ticker", "investor_type", "buy_value", "sell_value", "net_value"]
        if not frames:
            return pd.DataFrame(columns=cols)

        raw = pd.concat(frames, ignore_index=True)
        out = pd.DataFrame(
            {
                "date": trade_date,
                "ticker": raw.get("ISU_SRT_CD", raw.get("ISU_CD")).astype(str).str.strip(),
                "investor_type": raw["investor_type"],
                "buy_value": _num(raw.get("ASK_TRDVAL")),
                "sell_value": _num(raw.get("BID_TRDVAL")),
                "net_value": _num(raw.get("NETBID_TRDVAL")),
            }
        )
        return out.dropna(subset=["ticker"]).reset_index(drop=True)


def _num(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).replace({"-": None, "": None}),
        errors="coerce",
    )
