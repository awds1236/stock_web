"""현재가 조회 -- 화면이 스스로 갱신되게 만드는 최소 조각.

정직하게 짚어둘 것:

  1. **이것은 실시간 시세가 아닙니다.** 무료 소스가 주는 값은 지연 시세이며
     (통상 15분 이상), 거래에 쓸 수 있는 품질이 아닙니다. 그래서 응답에
     `source` 와 `note` 를 함께 실어 보내고, 화면은 이것을 반드시 표시합니다.

  2. **한국도 이제 당일 값을 받습니다.** 예전에는 이 파일이 미국만 다뤘고,
     한국은 "장중 소스가 없다"며 저장된 종가를 돌려줬습니다. 그런데 KRX
     Open API 에 장중 시세가 없다는 것과, *어떤* 무료 소스에도 없다는 것은
     다른 이야기입니다. 종목코드에 보드별 접미사를 붙이면(.KS / .KQ) 미국과
     **같은 소스**가 한국 종목의 지연 시세를 줍니다.

     한계도 같습니다 -- 비공식 경로라 예고 없이 막힐 수 있고, 지연 시세이며,
     보드를 모르면 접미사를 추측해야 합니다.

  3. **분당 1회를 넘지 않습니다.** 무료 소스가 차단으로 응답하게 만드는 것은
     대개 요청의 빈도입니다. 같은 종목을 60초 안에 다시 물으면 네트워크로
     나가지 않고 직전 응답을 돌려줍니다. 어차피 원천이 지연 시세라 더 자주
     물어도 같은 값입니다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveQuote:
    price: float | None
    previous_close: float | None
    currency: str | None
    source: str  # "live" | "unavailable"
    note: str


DELAY_NOTE = (
    "무료 소스의 지연 시세입니다(통상 15분 이상 지연). 참고용이며 체결 가격이 "
    "아닙니다."
)

KR_DELAY_NOTE = (
    "무료 소스의 한국 지연 시세입니다(통상 15~20분 지연). KRX 공식 실시간이 "
    "아니며 체결 가격도 아닙니다 -- 참고용입니다."
)

KR_NOTE = (
    "한국 종목의 당일 시세를 가져오지 못했습니다. 무료 소스는 비공식 경로라 "
    "차단되거나 규격이 바뀔 수 있습니다. 저장된 마지막 종가를 표시합니다."
)

# 같은 종목을 다시 묻기까지의 최소 간격.
#
# 화면·컴포넌트가 몇 개든, 사용자가 새로고침을 몇 번 누르든 이 값이 실제
# 네트워크 호출의 상한을 정합니다. 프론트엔드에도 같은 규칙이 있습니다
# (lib/liveQuote.ts). 양쪽 모두에 두는 이유는 두 경로가 서로를 통하지 않기
# 때문입니다 -- 브라우저 직접 호출은 백엔드를 거치지 않습니다.
QUOTE_MIN_INTERVAL_S = 60.0

_BOARD_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}

# 보드를 모를 때 시도할 순서. KOSPI 가 먼저인 이유는 종목 수와 조회 빈도가
# 그쪽이 많기 때문입니다 -- 첫 시도가 맞을 확률이 높은 순서로 둡니다.
_KR_FALLBACK_SUFFIXES = (".KS", ".KQ")


def quote_symbols(market: str, ticker: str, board: str | None = None) -> list[str]:
    """시세 소스에서 이 종목을 가리키는 심볼 후보 (앞이 우선).

    한국 종목코드는 그 자체로는 소스가 알아듣지 못합니다. 보드별 접미사가
    필요하고(.KS = 유가증권, .KQ = 코스닥), 보드를 모르면 둘 다 시도합니다.
    """
    ticker = ticker.strip()
    if market.upper() != "KR":
        return [ticker]
    suffix = _BOARD_SUFFIX.get((board or "").strip().upper())
    if suffix:
        return [f"{ticker}{suffix}"]
    return [f"{ticker}{s}" for s in _KR_FALLBACK_SUFFIXES]


class _QuoteCache:
    """심볼별 마지막 응답과 시각. 스레드 안전(배치 조회가 스레드 풀입니다)."""

    def __init__(self, min_interval_s: float = QUOTE_MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, LiveQuote]] = {}

    def get(self, key: str) -> LiveQuote | None:
        with self._lock:
            hit = self._entries.get(key)
        if hit is None:
            return None
        at, quote = hit
        if time.monotonic() - at >= self.min_interval_s:
            return None
        return quote

    def put(self, key: str, quote: LiveQuote) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), quote)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_cache = _QuoteCache()


def clear_quote_cache() -> None:
    """테스트·수동 갱신용. 운영 경로에서 부르지 마십시오."""
    _cache.clear()


def fetch_quote(market: str, ticker: str, board: str | None = None) -> LiveQuote:
    """한 종목의 지연 시세. 실패는 예외가 아니라 '없음'으로 돌려줍니다.

    현재가 조회가 실패했다고 종목 화면 전체가 깨지면 안 됩니다 -- 차트와 지표는
    저장된 데이터만으로 완전히 동작하며, 현재가는 부가 정보입니다.
    """
    market = market.upper()
    cache_key = f"{market}/{ticker}/{board or ''}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    quote = _fetch_uncached(market, ticker, board)
    _cache.put(cache_key, quote)
    return quote


def _fetch_uncached(market: str, ticker: str, board: str | None) -> LiveQuote:
    try:
        import yfinance as yf
    except ImportError:
        return LiveQuote(None, None, None, "unavailable", "yfinance 가 설치되지 않았습니다.")

    note = KR_DELAY_NOTE if market == "KR" else DELAY_NOTE
    last_error: Exception | None = None
    symbols = quote_symbols(market, ticker, board)

    for symbol in symbols:
        try:
            info = yf.Ticker(symbol).fast_info
            price = _pick(info, "last_price", "lastPrice")
            prev = _pick(info, "previous_close", "previousClose")
            currency = _pick(info, "currency", default=None, numeric=False)
        except Exception as exc:  # noqa: BLE001 -- 비공식 경로라 실패 형태가 다양합니다
            last_error = exc
            continue
        if price is None:
            # 접미사를 잘못 골랐을 때도 예외가 아니라 '값 없음'으로 옵니다.
            # 다음 후보가 있으면 그쪽을 시도합니다.
            continue
        return LiveQuote(price, prev, currency, "live", note)

    if last_error is not None:
        # 원본 예외를 함께 남기되, 그것만으로는 사용자가 무엇을 해야 할지 알 수
        # 없으므로 원인 후보를 같이 적습니다. 비공식 경로라 차단·규격변경·
        # 네트워크 어느 쪽이든 같은 모양으로 터집니다.
        return LiveQuote(None, None, None, "unavailable", _failure_note(last_error))
    return LiveQuote(
        None, None, None, "unavailable",
        f"시세 소스가 이 종목({'/'.join(symbols)})의 현재가를 주지 않았습니다 "
        "(상장폐지·거래정지 또는 종목코드 불일치일 수 있습니다).",
    )


def fetch_quotes(
    market: str,
    tickers: list[str],
    boards: dict[str, str] | None = None,
    *,
    max_workers: int = 8,
) -> dict[str, LiveQuote]:
    """여러 종목의 현재가를 한 번에.

    스레드 풀을 쓰는 이유는 `fetch_quote` 한 경로만 유지하기 위해서입니다.
    일괄 다운로드 API 를 따로 쓰면 단일 조회와 다른 코드로 다른 값을 만들 수
    있고, 그건 표와 상세 화면의 가격이 어긋나는 형태로 나타납니다.

    **호출자가 종목 수를 제한해야 합니다.** 이 함수는 받은 만큼 전부 요청합니다.
    캐시가 있으므로 60초 안의 반복 요청은 네트워크로 나가지 않습니다.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not tickers:
        return {}
    boards = boards or {}
    unique = list(dict.fromkeys(tickers))
    with ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as pool:
        results = list(
            pool.map(lambda t: fetch_quote(market, t, boards.get(t)), unique)
        )
    return dict(zip(unique, results, strict=True))


def _failure_note(exc: Exception) -> str:
    return (
        "현재가 소스(yfinance)에 접근하지 못했습니다. 네트워크 차단, 요청 한도, "
        "또는 비공식 API 규격 변경일 수 있습니다. 차트와 지표는 저장된 데이터로 "
        f"정상 동작합니다. (원인: {type(exc).__name__}: {exc})"
    )


def _pick(info, *keys: str, default=None, numeric: bool = True):
    """fast_info 는 버전에 따라 dict 이기도 하고 속성 객체이기도 합니다."""
    for key in keys:
        value = None
        try:
            value = info[key]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            value = getattr(info, key, None)
        if value is None:
            continue
        if not numeric:
            return str(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default
