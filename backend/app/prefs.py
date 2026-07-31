"""앱 환경설정 -- **비밀이 아닌** 값만 담습니다.

인증정보(`app/credentials.py`)와 왜 분리했는가:

    인증정보는 암호화 저장하고 조회 시 마스킹합니다. 모델 이름이나 자동 갱신
    주기까지 같은 취급을 하면, 화면에 값을 보여줄 수 없어 "지금 어떤 모델을
    쓰고 있는지" 조차 확인이 안 됩니다. 반대로 API 키를 여기 두면 평문 JSON
    으로 디스크에 남습니다. 둘의 요구가 정반대이므로 저장소를 나눕니다.

환경변수가 파일보다 **우선**합니다 -- 인증정보와 같은 규칙입니다. 배포에서
주입한 값이 UI 에 남아있던 옛 값에 덮이면 추적이 매우 어려운 사고가 됩니다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import settings

# 사용자가 지정한 기본 모델. 이 앱은 모델 목록을 하드코딩하지 않고 문자열을
# 그대로 API 에 전달합니다 -- 공급자가 모델을 추가·개명해도 코드 수정 없이
# 설정 화면에서 바꿀 수 있어야 하고, 존재하지 않는 이름이면 공급자의 오류
# 메시지를 그대로 사용자에게 보여주는 편이 정확하기 때문입니다.
DEFAULT_AI_MODEL = "gpt-5.6-luna"
DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"

MARKET_CODES = ("KR", "US")


class Preferences(BaseModel):
    """파일에 저장되는 사용자 설정."""

    ai_model: str = Field(default=DEFAULT_AI_MODEL, description="분석에 사용할 모델 이름")
    ai_base_url: str = Field(
        default=DEFAULT_AI_BASE_URL,
        description="OpenAI 호환 엔드포인트. 사설 게이트웨이나 프록시로 바꿀 수 있습니다.",
    )
    ai_max_output_tokens: int = Field(default=2000, ge=256, le=16000)
    ai_timeout_seconds: float = Field(default=120.0, ge=10.0, le=600.0)

    auto_refresh_enabled: bool = Field(
        default=False,
        description="주가 자동 수집. 기본은 꺼짐 -- 켜두면 앱을 켜자마자 외부 "
        "네트워크 호출이 시작되므로 사용자가 명시적으로 선택하게 합니다.",
    )
    auto_refresh_interval_minutes: int = Field(default=60, ge=5, le=1440)
    auto_refresh_markets: list[str] = Field(default_factory=lambda: ["US"])
    auto_refresh_lookback_days: int = Field(
        default=7,
        ge=2,
        le=90,
        description="증분 수집 시 다시 받아올 과거 일수. 며칠치를 겹쳐 받아야 "
        "휴장·지연 정정으로 생긴 구멍이 메워집니다.",
    )

    @field_validator("auto_refresh_markets")
    @classmethod
    def _known_markets(cls, v: list[str]) -> list[str]:
        out = [m.upper() for m in v]
        unknown = [m for m in out if m not in MARKET_CODES]
        if unknown:
            raise ValueError(f"알 수 없는 시장: {', '.join(unknown)}")
        # 중복은 조용히 제거합니다. 같은 시장을 두 번 넣으면 갱신이 두 번 돕니다.
        return list(dict.fromkeys(out))

    @field_validator("ai_base_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("엔드포인트는 http:// 또는 https:// 로 시작해야 합니다.")
        return v

    @field_validator("ai_model")
    @classmethod
    def _model_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("모델 이름이 비어 있습니다.")
        return v


# 환경변수로 덮어쓸 수 있는 항목. 값 파서를 함께 둡니다 -- 환경변수는 항상
# 문자열이므로 bool/int 를 그대로 넣으면 pydantic 이 아니라 이 계층에서
# 조용히 잘못된 타입이 들어갑니다.
_ENV_OVERRIDES: dict[str, tuple[str, Any]] = {
    "ai_model": ("AI_MODEL", str),
    "ai_base_url": ("AI_BASE_URL", str),
    "auto_refresh_enabled": ("AUTO_REFRESH_ENABLED", lambda s: s.lower() in ("1", "true", "yes")),
    "auto_refresh_interval_minutes": ("AUTO_REFRESH_INTERVAL_MINUTES", int),
}


class PreferenceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (settings.data_dir / "preferences.json")

    def _read_file(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 손상된 설정 파일 때문에 앱이 뜨지 않으면 안 됩니다. 기본값으로
            # 되돌리고 계속 진행합니다 -- 여기 담긴 값은 전부 재입력 가능합니다.
            return {}

    def load(self) -> Preferences:
        data = self._read_file()
        try:
            prefs = Preferences(**data)
        except Exception:  # noqa: BLE001 -- 스키마가 바뀌어도 앱은 떠야 합니다
            prefs = Preferences()
        return self._apply_env(prefs)

    @staticmethod
    def _apply_env(prefs: Preferences) -> Preferences:
        patch: dict[str, Any] = {}
        for field, (env_name, parse) in _ENV_OVERRIDES.items():
            raw = os.getenv(env_name, "").strip()
            if not raw:
                continue
            try:
                patch[field] = parse(raw)
            except (TypeError, ValueError):
                continue
        return prefs.model_copy(update=patch) if patch else prefs

    def env_controlled(self) -> list[str]:
        """환경변수가 이기고 있는 항목 이름. UI 가 편집 불가로 표시합니다."""
        return [
            field
            for field, (env_name, _) in _ENV_OVERRIDES.items()
            if os.getenv(env_name, "").strip()
        ]

    def save(self, patch: dict) -> Preferences:
        """부분 갱신. 보낸 필드만 바꿉니다."""
        current = Preferences(**self._read_file()) if self._read_file() else Preferences()
        merged = current.model_dump()
        merged.update({k: v for k, v in patch.items() if v is not None})
        prefs = Preferences(**merged)  # 검증은 여기서
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(prefs.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)  # 원자적 교체 -- 쓰다 만 JSON 이 남지 않게
        return self._apply_env(prefs)


_store: PreferenceStore | None = None


def get_prefs_store() -> PreferenceStore:
    global _store
    if _store is None:
        _store = PreferenceStore()
    return _store


def get_prefs() -> Preferences:
    return get_prefs_store().load()
