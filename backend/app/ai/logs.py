"""AI 분석 로그 -- 실행한 분석을 남겨 나중에 다시 볼 수 있게 합니다.

왜 남기는가:

  * **호출은 유료이고 결과는 재현되지 않습니다.** 같은 데이터로 다시 물어도
    같은 문장이 나오지 않으므로, 지우면 복구할 방법이 없습니다.
  * **나중에 맞았는지 확인할 수 있어야 합니다.** 3개월 전 분석이 무엇을
    근거로 무엇을 말했는지 볼 수 없다면, AI 서술은 검증 불가능한 인상으로만
    남습니다. 그래서 결과 텍스트뿐 아니라 **그때 넘긴 숫자(facts)** 를 함께
    저장합니다.
  * 실패한 호출도 기록합니다. "왜 안 됐지"를 나중에 추적할 수 있어야 합니다.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

from app.store import Store, get_store

LogKind = Literal["stock", "sector", "market"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_logs (
    id                VARCHAR PRIMARY KEY,
    created_at        TIMESTAMP NOT NULL,
    kind              VARCHAR NOT NULL,   -- stock | sector | market
    market            VARCHAR NOT NULL,
    subject           VARCHAR NOT NULL,   -- 종목코드 | 업종명 | 시장코드
    subject_label     VARCHAR,            -- 화면에 보여줄 이름
    model             VARCHAR,
    content           VARCHAR,            -- 마크다운 분석문
    facts             VARCHAR,            -- 그때 모델에 넘긴 숫자 (JSON)
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    duration_ms       INTEGER,
    error             VARCHAR             -- 실패했으면 이유, 성공이면 NULL
);
"""


@dataclass(frozen=True)
class LogEntry:
    id: str
    created_at: str
    kind: str
    market: str
    subject: str
    subject_label: str | None
    model: str | None
    content: str | None
    facts: dict | None
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int | None
    error: str | None


class AILogStore:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        return self._store or get_store()

    def _ensure(self, con) -> None:
        con.execute(SCHEMA)

    def add(
        self,
        *,
        kind: LogKind,
        market: str,
        subject: str,
        subject_label: str | None,
        model: str | None,
        content: str | None,
        facts: dict[str, Any] | None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> str:
        entry_id = uuid.uuid4().hex[:16]
        with self.store.cursor() as con:
            self._ensure(con)
            con.execute(
                "INSERT INTO ai_logs (id, created_at, kind, market, subject, "
                "subject_label, model, content, facts, prompt_tokens, "
                "completion_tokens, duration_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    entry_id,
                    datetime.now(UTC).replace(tzinfo=None),
                    kind,
                    market,
                    subject,
                    subject_label,
                    model,
                    content,
                    json.dumps(facts, ensure_ascii=False, default=str) if facts else None,
                    prompt_tokens,
                    completion_tokens,
                    duration_ms,
                    error,
                ],
            )
        return entry_id

    def list(
        self,
        *,
        kind: str | None = None,
        market: str | None = None,
        subject: str | None = None,
        include_failed: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LogEntry]:
        """목록. 본문(content)은 **미리보기만** 담아 돌려줍니다.

        전체 본문을 목록에 실으면 로그가 쌓일수록 응답이 수 MB 가 됩니다.
        전문은 상세 조회(`get`)에서 가져갑니다.
        """
        sql = "SELECT * FROM ai_logs WHERE 1=1"
        params: list = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if market:
            sql += " AND market = ?"
            params.append(market.upper())
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        if not include_failed:
            sql += " AND error IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        with self.store.cursor() as con:
            self._ensure(con)
            df = con.execute(sql, params).fetchdf()
        return [_to_entry(r, preview=True) for _, r in df.iterrows()]

    def get(self, entry_id: str) -> LogEntry | None:
        with self.store.cursor() as con:
            self._ensure(con)
            df = con.execute("SELECT * FROM ai_logs WHERE id = ?", [entry_id]).fetchdf()
        if df.empty:
            return None
        return _to_entry(df.iloc[0], preview=False)

    def delete(self, entry_id: str) -> bool:
        with self.store.cursor() as con:
            self._ensure(con)
            before = con.execute(
                "SELECT count(*) FROM ai_logs WHERE id = ?", [entry_id]
            ).fetchone()[0]
            con.execute("DELETE FROM ai_logs WHERE id = ?", [entry_id])
        return bool(before)

    def count(self) -> int:
        with self.store.cursor() as con:
            self._ensure(con)
            return int(con.execute("SELECT count(*) FROM ai_logs").fetchone()[0])


PREVIEW_CHARS = 220


def _to_entry(row, *, preview: bool) -> LogEntry:
    content = _s(row["content"])
    if preview and content and len(content) > PREVIEW_CHARS:
        content = content[:PREVIEW_CHARS] + "…"
    facts = None
    if not preview and _s(row["facts"]):
        try:
            facts = json.loads(row["facts"])
        except (json.JSONDecodeError, TypeError):
            facts = None
    return LogEntry(
        id=str(row["id"]),
        created_at=str(pd.Timestamp(row["created_at"]).isoformat()),
        kind=str(row["kind"]),
        market=str(row["market"]),
        subject=str(row["subject"]),
        subject_label=_s(row["subject_label"]),
        model=_s(row["model"]),
        content=content,
        facts=facts,
        prompt_tokens=_i(row["prompt_tokens"]),
        completion_tokens=_i(row["completion_tokens"]),
        duration_ms=_i(row["duration_ms"]),
        error=_s(row["error"]),
    )


def _s(v) -> str | None:
    return None if v is None or pd.isna(v) else str(v)


def _i(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


_logs: AILogStore | None = None


def get_ai_logs() -> AILogStore:
    global _logs
    if _logs is None:
        _logs = AILogStore()
    return _logs
