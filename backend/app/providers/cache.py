"""디스크 응답 캐시 + 일일 호출 예산 카운터.

KRX Open API는 일일 10,000회 제한이 있습니다. 히스토리 백필은 수천 일자를
훑으므로 캐시 없이는 한도를 태우고, 중간에 실패하면 처음부터 다시 받아야 합니다.
과거 일자의 확정 데이터는 불변이므로 영구 캐시가 안전합니다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.providers.base import CallBudgetExceeded


def _cache_key(namespace: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"ns": namespace, **params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class DiskCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.cache_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, params: dict[str, Any]) -> Path:
        d = self.root / namespace
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{_cache_key(namespace, params)}.json"

    def get(self, namespace: str, params: dict[str, Any]) -> Any | None:
        p = self._path(namespace, params)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # 부분 기록된 캐시는 버립니다.
            p.unlink(missing_ok=True)
            return None

    def set(self, namespace: str, params: dict[str, Any], value: Any) -> None:
        p = self._path(namespace, params)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)  # 원자적 교체 -- 중단되어도 반쪽 캐시가 남지 않습니다.


class CallBudget:
    """자정 기준으로 리셋되는 일일 호출 카운터."""

    def __init__(self, limit: int | None = None, state_path: Path | None = None) -> None:
        self.limit = limit if limit is not None else settings.krx_daily_call_budget
        self.state_path = state_path or (settings.data_dir / "call_budget.json")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> tuple[str, int]:
        if not self.state_path.exists():
            return date.today().isoformat(), 0
        try:
            raw = json.loads(self.state_path.read_text())
            return raw["day"], int(raw["count"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return date.today().isoformat(), 0

    def consume(self, n: int = 1) -> int:
        """호출 n회를 소비하고 남은 예산을 반환. 초과 시 예외."""
        today = date.today().isoformat()
        day, count = self._load()
        if day != today:
            day, count = today, 0
        if count + n > self.limit:
            raise CallBudgetExceeded(
                f"KRX 일일 호출 한도 초과: {count}+{n} > {self.limit}. "
                f"내일 재시도하거나 캐시를 활용하십시오."
            )
        count += n
        self.state_path.write_text(
            json.dumps({"day": day, "count": count, "updated": datetime.now().isoformat()})
        )
        return self.limit - count

    @property
    def remaining(self) -> int:
        today = date.today().isoformat()
        day, count = self._load()
        return self.limit if day != today else self.limit - count
