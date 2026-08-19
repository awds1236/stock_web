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
from app.providers.base import NotSubscribed, PriceProvider, ProviderError
from app.providers.cache import CallBudget, DiskCache

# 미신청 서비스일 때 KRX 응답 본문에 나타나는 신호들
_ERROR_HINTS = ("error", "err_msg", "errMsg", "resultCode", "OPP", "신청", "권한")


def _looks_like_error_payload(payload: dict[str, Any]) -> bool:
    """레코드 배열이 없고 에러성 키만 있으면 에러 응답으로 간주."""
    if any(isinstance(v, list) for v in payload.values()):
        return False
    keys = " ".join(payload.keys())
    return any(h.lower() in keys.lower() for h in _ERROR_HINTS)


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
    # MKT_NM 을 'market' 으로 받으면 안 됩니다. 저장소의 market 은 국가
    # 단위('KR')이고, 여기 오는 값은 그 안의 시장(KOSPI/KOSDAQ)입니다.
    # 예전에는 이 값이 저장 직전에 'KR' 로 덮여 사라졌고, 그래서 어떤 종목이
    # 코스피인지 코스닥인지 앱이 알 방법이 없었습니다.
    "MKT_NM": "board",
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
        # 인증정보는 앱 설정 화면에서 입력받아 암호화 저장소에 보관됩니다.
        # 환경변수가 설정되어 있으면 그쪽이 우선합니다(배포용).
        if auth_key is not None:
            self.auth_key = auth_key
        else:
            from app.credentials import get_credential

            self.auth_key = get_credential("KRX_AUTH_KEY")
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
                "KRX 인증키가 설정되지 않았습니다. 앱 설정 화면에서 입력하거나, "
                "배포 환경이라면 KRX_AUTH_KEY 환경변수로 주입하십시오. "
                "발급: https://openapi.krx.co.kr/ (인증키 신청 후 서비스별 이용신청 필요)"
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
            raise NotSubscribed(
                f"{endpoint.name}: 404. 엔드포인트가 없거나, 해당 서비스에 대한 "
                f"'API 이용신청'을 하지 않았을 수 있습니다."
            )
        if resp.status_code in (401, 403):
            raise NotSubscribed(
                f"{endpoint.name}: HTTP {resp.status_code} (권한 없음). "
                f"openapi.krx.co.kr > 서비스 이용 에서 이 API 의 '이용신청'을 "
                f"완료했는지 확인하십시오 -- 인증키 발급과 서비스별 이용신청은 별개입니다."
            )
        if resp.status_code != 200:
            raise ProviderError(
                f"{endpoint.name}: HTTP {resp.status_code} -- {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError(f"{endpoint.name}: JSON 파싱 실패 -- {resp.text[:200]}") from exc

        # KRX 는 미신청 서비스에 대해 200 + 에러 본문을 주기도 합니다. 이것을
        # '데이터 없음' 으로 오해하면 Phase 0 판정이 통째로 틀어집니다.
        if _looks_like_error_payload(payload):
            raise NotSubscribed(
                f"{endpoint.name}: 200 이지만 에러 응답 -- {str(payload)[:200]}"
            )

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

        for name, board in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
            ep = next(e for e in ENDPOINTS if e.name == name)
            payload = self.client.call(ep, {"basDd": bas_dd})
            rows = KrxOpenApiClient.rows_of(payload)
            if not rows:
                continue
            df = pd.DataFrame(rows).rename(columns=_PRICE_FIELD_MAP)
            # 보드는 **엔드포인트가 곧 답**입니다. 응답의 MKT_NM 은 같은
            # 유가증권 안에서도 세부 구분('KOSPI글로벌' 등)이 섞여 나올 수
            # 있는데, 우리가 필요한 건 .KS/.KQ 를 가르는 두 값뿐입니다.
            df["board"] = board
            frames.append(df)

        if not frames:
            return pd.DataFrame(
                columns=["date", "ticker", "name", "board", *_NUMERIC_COLS]
            )

        out = pd.concat(frames, ignore_index=True)
        keep = ["date", "ticker", "name", "board", *_NUMERIC_COLS]
        for col in keep:
            if col not in out.columns:
                out[col] = pd.NA
        out = out[keep]

        for col in _NUMERIC_COLS:
            out[col] = _to_number(out[col])
        out["date"] = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce").dt.date
        out["ticker"] = out["ticker"].astype(str).str.strip()
        return out.dropna(subset=["ticker", "date"]).reset_index(drop=True)
