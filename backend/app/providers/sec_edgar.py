"""SEC EDGAR Form 4 (내부자 매매) provider -- 미국 '수급'의 대체물.

성격을 먼저 분명히 합니다. Form 4는 **기관 수급이 아닙니다.** 임원·이사·10% 이상
주주가 자기 회사 주식을 사고판 기록입니다. 한국의 연기금 순매수와는 재는 대상이
다르므로, 두 시장의 시그널을 나란히 놓고 우열을 비교하면 안 됩니다.

무엇이 근거가 있는가:
    * **매수(코드 P)** 는 Lakonishok & Lee(2001) 이래 6~12개월 초과수익 4~8% 가
      일관되게 보고되었습니다.
    * **매도는 예측력이 약하거나 없습니다.** 내부자는 분산투자·세금·유동성 등
      정보와 무관한 이유로 팔기 때문입니다. 그래서 이 provider 는 기본적으로
      매수만 수집합니다.
    * 결정적 반론: 신호당 거래금액을 현실적 규모로 제한하면 **초과수익이 사라지거나
      음수로 뒤집힌다**는 연구가 있습니다. 이 앱의 거래량 제약과 정확히 같은
      문제이며, 가설 H7에서 직접 검증합니다.

SEC 접근 규칙 (반드시 준수):
    * User-Agent 헤더에 실제 연락처를 넣어야 합니다. 없으면 차단됩니다.
    * 초당 10회 이하로 제한됩니다.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import pandas as pd

from app.config import settings
from app.providers.base import ProviderError
from app.providers.cache import DiskCache

EDGAR_BASE = "https://www.sec.gov"
DAILY_INDEX = EDGAR_BASE + "/Archives/edgar/daily-index/{year}/QTR{qtr}/form.{ymd}.idx"

# Form 4 거래 코드. 예측력 근거가 있는 것은 P(공개시장 매수)입니다.
CODE_OPEN_MARKET_BUY = "P"
CODE_OPEN_MARKET_SELL = "S"
# A(수여), M(옵션행사), G(증여) 등은 본인 판단에 의한 매수가 아니므로 제외합니다.
# 이것을 섞으면 스톡옵션 부여가 '내부자 매수'로 둔갑해 시그널이 오염됩니다.
MEANINGFUL_CODES = (CODE_OPEN_MARKET_BUY, CODE_OPEN_MARKET_SELL)


@dataclass(frozen=True)
class InsiderTransaction:
    filed_date: date
    transaction_date: date
    ticker: str
    issuer_cik: str
    insider_name: str
    is_officer: bool
    is_director: bool
    is_ten_pct_owner: bool
    code: str
    shares: float
    price: float

    @property
    def value(self) -> float:
        return self.shares * self.price


class SecEdgarClient:
    def __init__(
        self,
        user_agent: str | None = None,
        *,
        cache: DiskCache | None = None,
        requests_per_second: float = 8.0,
        timeout: float = 30.0,
    ) -> None:
        self.user_agent = user_agent or settings.sec_user_agent
        if not self.user_agent or "@" not in self.user_agent:
            raise ProviderError(
                "SEC 는 User-Agent 에 실제 연락처를 요구합니다. backend/.env 에 "
                "SEC_USER_AGENT='Your Name your@email.com' 형식으로 설정하십시오. "
                "없으면 SEC 가 요청을 차단합니다."
            )
        self.cache = cache or DiskCache()
        self._min_interval = 1.0 / requests_per_second
        self._last_call = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SecEdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _get_text(self, url: str, *, cache_ns: str | None = None) -> str:
        if cache_ns:
            hit = self.cache.get(cache_ns, {"url": url})
            if hit is not None:
                return hit
        self._throttle()
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderError(f"SEC 호출 실패 {url}: {exc}") from exc
        if resp.status_code == 404:
            raise ProviderError(f"SEC 404: {url}")
        if resp.status_code == 403:
            raise ProviderError(
                f"SEC 403 -- User-Agent 가 거부되었습니다. 실제 연락처를 넣으십시오. ({url})"
            )
        if resp.status_code != 200:
            raise ProviderError(f"SEC HTTP {resp.status_code}: {url}")
        if cache_ns:
            self.cache.set(cache_ns, {"url": url}, resp.text)
        return resp.text

    def list_form4_filings(self, filed_date: date) -> list[str]:
        """해당 날짜에 접수된 Form 4 문서 경로 목록."""
        url = DAILY_INDEX.format(
            year=filed_date.year,
            qtr=(filed_date.month - 1) // 3 + 1,
            ymd=filed_date.strftime("%Y%m%d"),
        )
        try:
            text = self._get_text(url, cache_ns="sec_daily_index")
        except ProviderError:
            # 주말·휴일은 인덱스가 없습니다. 빈 목록이 정상입니다.
            return []

        paths: list[str] = []
        for line in text.splitlines():
            # 형식: Form Type | Company Name | CIK | Date Filed | File Name
            if not line.startswith("4 "):
                continue
            parts = line.split()
            if parts and parts[-1].endswith(".txt"):
                paths.append(parts[-1])
        return paths

    def fetch_transactions(self, filed_date: date) -> list[InsiderTransaction]:
        """해당 접수일의 Form 4 거래 내역.

        주의: 접수일(filed_date)과 거래일(transaction_date)은 다릅니다. Form 4는
        거래 후 2영업일 내 제출이므로 보통 1~2일 차이가 납니다. **시그널 시점은
        반드시 접수일 기준**이어야 합니다 -- 거래일 기준으로 시그널을 만들면
        공시되기 전 정보를 쓴 것이 되어 look-ahead 누수입니다.
        """
        out: list[InsiderTransaction] = []
        for path in self.list_form4_filings(filed_date):
            try:
                doc = self._get_text(f"{EDGAR_BASE}/Archives/{path}", cache_ns="sec_form4")
            except ProviderError:
                continue
            out.extend(parse_form4(doc, filed_date))
        return out


def parse_form4(document: str, filed_date: date) -> list[InsiderTransaction]:
    """Form 4 제출 문서(.txt 래퍼)에서 XML 을 뽑아 거래를 파싱합니다."""
    start = document.find("<ownershipDocument>")
    end = document.find("</ownershipDocument>")
    if start == -1 or end == -1:
        return []

    try:
        root = ET.fromstring(document[start : end + len("</ownershipDocument>")])
    except ET.ParseError:
        return []

    ticker = _text(root, "./issuer/issuerTradingSymbol") or ""
    cik = _text(root, "./issuer/issuerCik") or ""
    name = _text(root, "./reportingOwner/reportingOwnerId/rptOwnerName") or ""
    rel = root.find("./reportingOwner/reportingOwnerRelationship")

    results: list[InsiderTransaction] = []
    for txn in root.findall("./nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "./transactionCoding/transactionCode")
        if code not in MEANINGFUL_CODES:
            continue

        shares = _float(txn, "./transactionAmounts/transactionShares/value")
        price = _float(txn, "./transactionAmounts/transactionPricePerShare/value")
        txn_date_raw = _text(txn, "./transactionDate/value")
        if shares is None or price is None or not txn_date_raw:
            continue
        # 가격 0은 공개시장 거래가 아닙니다(증여·수여 등이 P/S로 잘못 코딩된 경우).
        if price <= 0 or shares <= 0:
            continue

        try:
            txn_date = date.fromisoformat(txn_date_raw[:10])
        except ValueError:
            continue

        results.append(
            InsiderTransaction(
                filed_date=filed_date,
                transaction_date=txn_date,
                ticker=ticker.upper().strip(),
                issuer_cik=cik,
                insider_name=name,
                is_officer=_flag(rel, "isOfficer"),
                is_director=_flag(rel, "isDirector"),
                is_ten_pct_owner=_flag(rel, "isTenPercentOwner"),
                code=code,
                shares=shares,
                price=price,
            )
        )
    return results


def transactions_to_frame(txns: list[InsiderTransaction]) -> pd.DataFrame:
    cols = [
        "filed_date",
        "transaction_date",
        "ticker",
        "insider_name",
        "is_officer",
        "is_director",
        "is_ten_pct_owner",
        "code",
        "shares",
        "price",
        "value",
    ]
    if not txns:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(
        [
            {
                "filed_date": t.filed_date,
                "transaction_date": t.transaction_date,
                "ticker": t.ticker,
                "insider_name": t.insider_name,
                "is_officer": t.is_officer,
                "is_director": t.is_director,
                "is_ten_pct_owner": t.is_ten_pct_owner,
                "code": t.code,
                "shares": t.shares,
                "price": t.price,
                "value": t.value,
            }
            for t in txns
        ]
    )[cols]


def business_days(start: date, end: date):
    """주말을 건너뛴 날짜 이터레이터 (미국 공휴일은 인덱스 부재로 자연 처리됨)."""
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else None


def _float(node: ET.Element, path: str) -> float | None:
    raw = _text(node, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _flag(node: ET.Element | None, tag: str) -> bool:
    raw = _text(node, f"./{tag}")
    return raw in ("1", "true", "True")
