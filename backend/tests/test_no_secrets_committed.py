"""저장소에 비밀정보가 커밋되었는지 검사.

pre-commit 훅은 설치해야 동작하고 `--no-verify` 로 우회됩니다. 이 테스트는
**이미 추적 중인 파일 전체**를 검사하므로, 훅을 안 깔았거나 우회했거나 다른
기기에서 커밋한 경우에도 걸립니다.

테스트 스위트에 있는 이유: 비밀정보 유출은 한 번 푸시되면 히스토리에 영구히
남고, 키를 폐기·재발급하는 것 외에는 되돌릴 방법이 없습니다. 사후에 발견하는
것보다 CI 에서 막는 편이 훨씬 쌉니다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 값이 채워지면 안 되는 변수들.
# `\s` 대신 `[^\S\n]` (개행을 뺀 공백)을 쓰는 것이 중요합니다. `\s*` 는 개행을
# 먹기 때문에 `KRX_AUTH_KEY=` 처럼 값이 빈 줄에서 **다음 줄 내용을 값으로 오인**해
# 오탐을 냅니다.
H = r"[^\S\n]"
SECRET_VAR = re.compile(
    rf"^{H}*(?:export{H}+)?"
    r"(KRX_AUTH_KEY|SEC_USER_AGENT|\w*_API_KEY|\w*_SECRET|\w*_TOKEN|\w*_PASSWORD)"
    rf"{H}*={H}*([^\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)

# 자리표시자로 인정하는 값 -- 실제 비밀이 아님
PLACEHOLDER = re.compile(
    r"^(|\"\"|''|your.*|change.*|x{3,}|<.*>|\.{3}.*|\$\d+.*"
    r"|.*your@email\.com.*|.*example\.com.*"
    r"|발급받은키.*|.*여기에.*|\{\{.*\}\}|\$\{.*\}|os\.getenv.*|Field\(.*)$",
    re.IGNORECASE,
)

# 소스코드에서 변수 '정의'가 아니라 '참조'인 경우는 제외
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".sh", ".yml", ".yaml"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / p for p in out.stdout.split("\0") if p]


def test_no_env_file_is_tracked():
    """.env 는 어떤 경로에서도 추적되면 안 됩니다."""
    offenders = [
        f.relative_to(REPO_ROOT)
        for f in tracked_files()
        if f.name == ".env" or (f.name.startswith(".env.") and f.name != ".env.example")
    ]
    assert not offenders, f".env 파일이 커밋되어 있습니다: {offenders}"


def test_no_filled_secret_values_in_tracked_files():
    """추적 중인 파일에 값이 채워진 비밀 변수가 없어야 합니다."""
    offenders: list[str] = []

    for path in tracked_files():
        if path.suffix in {".lock", ".png", ".jpg", ".ico"}:
            continue
        # 이 테스트 파일 자신은 패턴을 포함하므로 제외
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue

        for match in SECRET_VAR.finditer(text):
            var, raw = match.group(1), match.group(2).strip()
            # 줄 끝 주석은 값이 아닙니다 (`KEY=...  # 설명`)
            value = raw.split("#", 1)[0].strip().strip("\"'").strip()
            # 코드 파일에서 기본값이 빈 문자열인 선언은 정상
            if path.suffix in CODE_SUFFIXES and (
                "getenv" in raw or "Field(" in raw or "settings." in raw
            ):
                continue
            if PLACEHOLDER.match(value):
                continue
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no} {var}=***")

    assert not offenders, "비밀정보로 보이는 값이 커밋되어 있습니다:\n" + "\n".join(
        offenders
    )


def test_env_example_keeps_secrets_blank():
    """템플릿에는 실제 키가 들어가면 안 됩니다."""
    example = REPO_ROOT / "backend" / ".env.example"
    assert example.exists(), ".env.example 이 있어야 합니다 (설정 문서 역할)"

    for line in example.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("KRX_AUTH_KEY="):
            value = line.split("=", 1)[1].strip()
            assert value == "", f".env.example 의 KRX_AUTH_KEY 는 비워야 합니다: {value!r}"


def test_gitignore_covers_env():
    """.gitignore 가 .env 를 실제로 무시하는지 git 에게 직접 확인."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "backend/.env"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, "backend/.env 가 .gitignore 에 걸리지 않습니다"


def test_data_dir_is_ignored():
    """API 응답 캐시에는 조회 결과가 남습니다. 커밋되면 안 됩니다."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "backend/data/cache"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, "backend/data/ 가 .gitignore 에 걸리지 않습니다"


@pytest.mark.parametrize("hook", ["pre-commit"])
def test_hook_exists_and_is_executable(hook: str):
    """훅 파일이 저장소에 있고 실행 가능해야 설치가 의미를 가집니다."""
    path = REPO_ROOT / ".githooks" / hook
    assert path.exists(), f".githooks/{hook} 이 없습니다"
    assert path.stat().st_mode & 0o111, f".githooks/{hook} 에 실행 권한이 없습니다"
