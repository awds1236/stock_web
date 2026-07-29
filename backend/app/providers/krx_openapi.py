"""KRX Open API 클라이언트 (openapi.krx.co.kr).

인증: `AUTH_KEY` HTTP 헤더. 일일 10,000회 제한.

주의 -- 미확정 사항:
    이 API의 주식 카테고리에 **종목별 투자자별 매매동향(연기금 포함)** 엔드포인트가
    존재하는지는 공개 문서만으로 확정되지 않았습니다. 일별 시세와 종목기본정보는
    확인되지만 수급은 확인되지 않았습니다. `scripts/probe_krx.py` 를 인증키와 함께
    실행해 판정한 뒤, 없으면 `krx_mdc.KrxMdcFlowProvider` 로 대체합니다.

    아래 ENDPOINTS 는 그 판정을 위한 **후보 목록**이며, 확인된 것과 추정인 것을
    구분해 표시했습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import pandas as pd

from app.config import settings
from app.providers.base import PriceProvider, ProviderError
from app.providers.cache import CallBudget, DiskCache


@dataclass(frozen=True)
class Endpoint:
    group: str  # URL 경로 그룹 (sto, idx, etp ...)
    name: str
    label: str
    confirmed: bool  # 공개 문서로 확인된 엔드포인트인가


# 확인됨(confirmed=True): 웹 문서/블로그에서 실제 사용 사례가 확인된 것
# 추정(confirmed=False): 명명 규칙으로 추론한 것 -- probe 로 검증 필요
ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("sto", "stk_bydd_trd", "유가증권 일별매매정보", True),
    Endpoint("sto", "ksq_bydd_trd", "코스닥 일별매매정보", True),
    Endpoint("sto", "knx_bydd_trd", "코넥스 일별매매정보", False),
    Endpoint("sto", "stk_isu_base_info", "유가증권 종목기본정보", True),
    Endpoint("sto", "ksq_isu_base_info", "코스닥 종목기본정보", False),
    Endpoint("sto", "knx_isu_base_info", "코넥스 종목기본정보", False),
    # 아래는 '투자자별 수급이 Open API 에 있는가' 를 판정하기 위한 후보.
    # 전부 추정이며 404 가 나오면 MDC 대체 경로를 택합니다.
    Endpoint("sto", "stk_invst_trd", "유가증권 투자자별 매매동향(추정)", False),
    Endpoint("sto", "ksq_invst_trd", "코스닥 투자자별 매매동향(추정)", False),
    Endpoint("sto", "stk_invstr_trd", "유가증권 투자자별(대체 표기, 추정)", False),
    Endpoint("idx", "kospi_dd_trd", "KOSPI 지수 일별시세", True),
    Endpoint("idx", "kosdaq_dd_trd", "KOSDAQ 지수 일별시세", True),
)

# 시세 응답의 KRX 필드명 → 내부 스키마
_PRICE_FIELD_MAP = {
    "BAS_DD": "date",
    "ISU_CD": "ticker",
    "ISU_NM": "name",
    "MKT_NM": "market",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "MKTCAP": "market_cap",
    "LIST_SHRS": "shares",
}

_NUMERIC_COLS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "market_cap",
    "shares",
)


def _to_number(series: pd.Series) -> pd.Series:
    """KRX 응답은 숫자를 '1,234' 형태 문자열로 줍니다. '-' 는 결측."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).replace({"-": None, "": None}),
        errors="coerce",
    )


class KrxOpenApiClient:
    def __init__(
        self,
        auth_key: str | None = None,
        *,
        cache: DiskCache | None = None,
        budget: CallBudget | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.auth_key = auth_key if auth_key is not None else settings.krx_auth_key
        self.cache = cache or DiskCache()
        self.budget = budget or CallBudget()
        self._client = httpx.Client(
            timeout=timeout,
            headers={"AUTH_KEY": self.auth_key, "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KrxOpenApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(
        self,
        endpoint: Endpoint,
        params: dict[str, Any],
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """엔드포인트 1회 호출. 캐시 적중 시 호출 예산을 소비하지 않습니다."""
        if not self.auth_key:
            raise ProviderError(
                "KRX_AUTH_KEY 가 설정되지 않았습니다. openapi.krx.co.kr 에서 인증키를 "
                "발급받아 backend/.env 에 KRX_AUTH_KEY=... 로 넣으십시오."
            )

        ns = f"krx_openapi/{endpoint.group}/{endpoint.name}"
        if use_cache:
            hit = self.cache.get(ns, params)
            if hit is not None:
                return hit

        self.budget.consume(1)
        url = f"{settings.krx_openapi_base}/{endpoint.group}/{endpoint.name}"
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{endpoint.name} 호출 실패: {exc}") from exc

        if resp.status_code == 404:
            raise ProviderError(f"{endpoint.name}: 존재하지 않는 엔드포인트 (404)")
        if resp.status_code != 200:
            raise ProviderError(
                f"{endpoint.name}: HTTP {resp.status_code} -- {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"{endpoint.name}: JSON 파싱 실패 -- {resp.text[:200]}") from exc

        if use_cache:
            self.cache.set(ns, params, payload)
        return payload

    @staticmethod
    def rows_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """KRX 응답 본문에서 레코드 배열을 꺼냅니다.

        응답 래퍼 키는 엔드포인트마다 다르므로(OutBlock_1 등) 첫 번째 리스트 값을
        찾아 반환합니다.
        """
        for value in payload.values():
            if isinstance(value, list):
                return value
        return []


class KrxOpenApiPriceProvider(PriceProvider):
    """KRX Open API 기반 일별 시세 provider (KOSPI + KOSDAQ 병합)."""

    def __init__(self, client: KrxOpenApiClient | None = None) -> None:
        self.client = client or KrxOpenApiClient()

    def fetch_daily_prices(self, trade_date: date) -> pd.DataFrame:
        bas_dd = trade_date.strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []

        for name, market in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
            ep = next(e for e in ENDPOINTS if e.name == name)
            payload = self.client.call(ep, {"basDd": bas_dd})
            rows = KrxOpenApiClient.rows_of(payload)
            if not rows:
                continue
            df = pd.DataFrame(rows).rename(columns=_PRICE_FIELD_MAP)
            df["market"] = df.get("market", market)
            frames.append(df)

        if not frames:
            return pd.DataFrame(
                columns=["date", "ticker", "name", "market", *_NUMERIC_COLS]
            )

        out = pd.concat(frames, ignore_index=True)
        keep = ["date", "ticker", "name", "market", *_NUMERIC_COLS]
        for col in keep:
            if col not in out.columns:
                out[col] = pd.NA
        out = out[keep]

        for col in _NUMERIC_COLS:
            out[col] = _to_number(out[col])
        out["date"] = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce").dt.date
        out["ticker"] = out["ticker"].astype(str).str.strip()
        return out.dropna(subset=["ticker", "date"]).reset_index(drop=True)
