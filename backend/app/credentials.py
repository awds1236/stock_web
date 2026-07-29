"""API 인증정보 저장소 -- 앱 안에서 입력받아 암호화 보관.

키를 `.env` 파일에 두는 대신 앱 설정 화면에서 입력받습니다. 저장 위치는
저장소 바깥(`backend/data/`, .gitignore 대상)이며 파일 권한은 0600 입니다.

이 방식이 실제로 막아주는 것과 막지 못하는 것을 분명히 해둡니다:

  막아줍니다:
    * 실수로 git 에 커밋되는 것 (저장소 바깥 + gitignore + 훅 + 테스트)
    * 백업·화면공유·로그에 평문으로 노출되는 것
    * 다른 사용자 계정이 파일을 읽는 것 (0600)

  막지 못합니다:
    * **서버 파일시스템에 접근 가능한 사람.** 암호화 키가 같은 머신에 있으므로
      둘 다 읽으면 복호화됩니다. 이것은 '앱이 재시작 후에도 키를 쓸 수 있어야
      한다'는 요구와 근본적으로 상충하며, 로컬 단일 사용자 도구에서는 통상
      받아들이는 절충입니다.
    * **인증 없이 공개 배포하는 경우.** 설정 API 가 그대로 인증정보 입력·조회
      창구가 됩니다. 다중 사용자로 배포한다면 반드시 인증을 먼저 붙이십시오.

배포 시에는 환경변수를 쓰는 편이 낫고, 환경변수가 저장소보다 **우선**합니다.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

CredentialName = Literal["KRX_AUTH_KEY", "SEC_USER_AGENT"]

# 앱이 관리하는 인증정보 목록. 여기 없는 이름은 저장을 거부합니다 -- 임의의
# 키/값 저장소가 되면 무엇이 비밀인지 추적할 수 없게 됩니다.
MANAGED: dict[str, dict[str, str]] = {
    "KRX_AUTH_KEY": {
        "label": "KRX Open API 인증키",
        "help": (
            "openapi.krx.co.kr 회원가입 → 마이페이지 → API 인증키 신청 (24시간 내 승인). "
            "인증키 발급과 서비스별 '이용신청'은 별개이므로 둘 다 완료해야 합니다."
        ),
        "signup_url": "https://openapi.krx.co.kr/",
    },
    "SEC_USER_AGENT": {
        "label": "SEC EDGAR 연락처",
        "help": (
            "미국 내부자 매매(Form 4) 조회에 필요합니다. SEC 는 API 키를 발급하지 "
            "않는 대신 '이름 email@example.com' 형식의 실제 연락처를 요구하며, "
            "없으면 요청을 차단합니다."
        ),
        "signup_url": "https://www.sec.gov/os/webmaster-faq#developers",
    },
}


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialStatus:
    name: str
    label: str
    help: str
    signup_url: str
    configured: bool
    source: Literal["env", "stored", "none"]
    masked: str
    editable: bool  # 환경변수로 설정된 값은 UI 에서 바꿀 수 없습니다


def mask(value: str) -> str:
    """저장된 값을 그대로 돌려주지 않습니다.

    설정 화면은 '키가 들어있는지' 확인할 수 있으면 충분합니다. 전체 값을
    API 로 내보내면 저장소 밖으로 뺀 의미가 없어집니다.
    """
    if not value:
        return ""
    if "@" in value:  # SEC 연락처는 이메일 도메인만 남깁니다
        local, _, domain = value.rpartition("@")
        head = local.strip()[:3]
        return f"{head}***@{domain}"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


class CredentialStore:
    """Fernet 대칭 암호화 저장소."""

    def __init__(
        self,
        store_path: Path | None = None,
        key_path: Path | None = None,
    ) -> None:
        self.store_path = store_path or (settings.data_dir / "credentials.enc")
        self.key_path = key_path or (settings.data_dir / ".credential_key")

    # ── 암호화 키 ────────────────────────────────────────────────────────
    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            # 파일을 만든 뒤 권한을 바꾸면 그 사이에 읽힐 수 있으므로,
            # 0600 으로 열어서 생성합니다.
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
        else:
            self._require_private(self.key_path)
        return Fernet(self.key_path.read_bytes())

    @staticmethod
    def _require_private(path: Path) -> None:
        """권한이 느슨해졌으면 조용히 넘어가지 않고 알립니다.

        Windows(NTFS)에서는 이 검사를 건너뜁니다. NTFS 는 POSIX 권한 비트를
        쓰지 않아 Python 의 st_mode 가 모든 파일을 '그룹/기타 읽기 가능'으로
        보고하므로, 검사를 그대로 적용하면 **정상 파일까지 전부 거부**됩니다
        (실사용에서 확인된 버그). Windows 의 접근 제어는 NTFS ACL 과 사용자
        프로필 디렉터리가 담당합니다.
        """
        if os.name == "nt":
            return
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise CredentialError(
                f"{path} 의 권한이 너무 개방적입니다 (다른 사용자가 읽을 수 있음). "
                f"`chmod 600 {path}` 로 고치십시오."
            )

    # ── 읽기/쓰기 ────────────────────────────────────────────────────────
    def _read_all(self) -> dict[str, str]:
        if not self.store_path.exists():
            return {}
        self._require_private(self.store_path)
        try:
            raw = self._fernet().decrypt(self.store_path.read_bytes())
        except InvalidToken as exc:
            raise CredentialError(
                f"{self.store_path} 를 복호화할 수 없습니다. 암호화 키"
                f"({self.key_path})가 교체되었거나 파일이 손상되었습니다. "
                "저장된 인증정보를 지우고 다시 입력하십시오."
            ) from exc
        return json.loads(raw.decode("utf-8"))

    def _write_all(self, data: dict[str, str]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._fernet().encrypt(json.dumps(data, ensure_ascii=False).encode())
        tmp = self.store_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        tmp.replace(self.store_path)  # 원자적 교체

    # ── 공개 API ─────────────────────────────────────────────────────────
    def set(self, name: str, value: str) -> None:
        if name not in MANAGED:
            raise CredentialError(f"관리 대상이 아닌 인증정보: {name!r}")
        value = value.strip()
        if not value:
            raise CredentialError(f"{name} 값이 비어 있습니다")
        validate(name, value)

        data = self._read_all()
        data[name] = value
        self._write_all(data)

    def delete(self, name: str) -> bool:
        data = self._read_all()
        if name not in data:
            return False
        del data[name]
        self._write_all(data)
        return True

    def get_stored(self, name: str) -> str:
        return self._read_all().get(name, "")

    def resolve(self, name: str) -> str:
        """실제로 사용할 값. **환경변수가 저장소보다 우선합니다.**

        배포 환경에서 환경변수로 주입한 값이 UI 에 남아있던 옛 값에 덮이면
        추적이 매우 어려운 사고가 됩니다. 12-factor 관례대로 환경변수를
        명시적 상위 설정으로 취급합니다.
        """
        env = os.getenv(name, "").strip()
        if env:
            return env
        return self.get_stored(name)

    def status(self, name: str) -> CredentialStatus:
        meta = MANAGED[name]
        env = os.getenv(name, "").strip()
        stored = self.get_stored(name)
        value = env or stored
        source: Literal["env", "stored", "none"] = (
            "env" if env else ("stored" if stored else "none")
        )
        return CredentialStatus(
            name=name,
            label=meta["label"],
            help=meta["help"],
            signup_url=meta["signup_url"],
            configured=bool(value),
            source=source,
            masked=mask(value),
            # 환경변수가 이기므로, UI 에서 고쳐봐야 반영되지 않습니다.
            # 편집 불가로 표시해 혼란을 막습니다.
            editable=source != "env",
        )

    def status_all(self) -> list[CredentialStatus]:
        return [self.status(name) for name in MANAGED]


def validate(name: str, value: str) -> None:
    """저장 전 형식 검사.

    잘못된 형식을 저장해두면 나중에 원격 호출이 실패할 때 원인이 인증정보인지
    네트워크인지 구분하기 어렵습니다. 입력 시점에 걸러냅니다.
    """
    if name == "SEC_USER_AGENT":
        if "@" not in value or "." not in value.rpartition("@")[2]:
            raise CredentialError(
                "SEC 는 실제 연락 가능한 이메일을 요구합니다. "
                "'이름 email@example.com' 형식으로 입력하십시오."
            )
        if value.strip().startswith("@"):
            raise CredentialError("이름과 이메일을 함께 입력하십시오.")
    elif name == "KRX_AUTH_KEY":
        if len(value) < 8:
            raise CredentialError("KRX 인증키가 너무 짧습니다. 값을 다시 확인하십시오.")
        if any(c.isspace() for c in value):
            raise CredentialError("KRX 인증키에 공백이 포함되어 있습니다.")


# 애플리케이션 전역 저장소
store = CredentialStore()


def get_credential(name: str) -> str:
    """provider 들이 사용하는 진입점."""
    return store.resolve(name)
