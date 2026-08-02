"""업종명 → 파일명 규칙.

정적 배포에서 **모든 업종의 분석 버튼이 실패**했던 버그를 고정합니다.

원인:
    파일명을 `quote(name)` 으로 만들었더니 '건설' 이 `%EA%B1%B4%EC%84%A4`
    라는 이름의 파일이 됐습니다. 브라우저가 그 URL 을 요청하면 웹서버가
    퍼센트 인코딩을 **디코딩해서** '건설' 파일을 찾으므로, 디스크의
    `%EA%B1%B4...` 파일은 영원히 404 입니다. 이중 인코딩으로만 열리는데
    브라우저는 그렇게 요청하지 않습니다.

    한국만의 문제가 아니었습니다. 'Banks - Diversified' 는 공백이 `%20` 이
    되어 미국도 똑같이 깨져 있었습니다.

여기서 지키는 것:
    1. 결과는 **순수 ASCII** -- URL 디코딩이 항등이어야 합니다.
    2. 파일시스템에 위험한 문자가 남지 않습니다.
    3. **프론트엔드 구현과 정확히 일치**합니다 (실제로 node 로 돌려 대조).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote

import pytest

from scripts.export_static import _slug

REPO_ROOT = Path(__file__).resolve().parents[2]
API_TS = REPO_ROOT / "frontend" / "src" / "lib" / "api.ts"

NAMES = [
    "건설",
    "반도체",
    "은행",
    "Banks - Diversified",
    "Semiconductors",
    "Oil & Gas Integrated",
    "Software - Infrastructure",
    "전기·전자",
    "IT 서비스",
    "Farm & Heavy Construction Machinery",
    "a/b",  # 경로 구분자가 하위 디렉터리를 만들면 안 됩니다
    "..",
    "100%",
    "café",
]


class TestSlugShape:
    @pytest.mark.parametrize("name", NAMES)
    def test_result_is_pure_ascii(self, name):
        """ASCII 가 아니면 호스트별 유니코드 정규화(NFC/NFD)로 깨집니다."""
        assert _slug(name).isascii()

    @pytest.mark.parametrize("name", NAMES)
    def test_url_decoding_is_identity(self, name):
        """이것이 핵심입니다 -- 요청 URL 이 디코딩돼도 같은 이름이어야 합니다."""
        slug = _slug(name)
        assert unquote(slug) == slug

    @pytest.mark.parametrize("name", NAMES)
    def test_no_path_separators_or_percent(self, name):
        slug = _slug(name)
        assert "/" not in slug and "\\" not in slug
        assert "%" not in slug, "퍼센트가 남으면 디코딩 왕복에서 깨집니다"

    def test_traversal_characters_cannot_survive(self):
        """업종명은 외부에서 온 문자열입니다. 경로를 벗어날 수 없어야 합니다."""
        slug = _slug("../../etc/passwd")
        assert "/" not in slug and "\\" not in slug
        # 항상 접두사·확장자 사이에 들어가므로 경로 조작이 성립하지 않습니다.
        assert "/" not in f"analysis-sector-KR-{slug}.json"

    def test_distinct_names_get_distinct_files(self):
        """충돌하면 한 업종의 분석이 다른 업종 것으로 덮입니다."""
        slugs = [_slug(n) for n in NAMES]
        assert len(set(slugs)) == len(slugs)

    def test_ascii_names_stay_readable(self):
        assert _slug("Semiconductors") == "Semiconductors"


class TestMatchesFrontend:
    """두 구현이 갈리면 **정적 배포에서만** 404 가 납니다.

    로컬 개발은 백엔드를 직접 호출하므로 이 경로를 타지 않아, 사람이 눈으로
    비교하는 것만으로는 어긋난 것을 알아채지 못합니다. 그래서 실제로 실행해
    대조합니다.
    """

    def test_typescript_and_python_agree(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node 가 없어 프론트엔드 구현과 대조할 수 없습니다")

        source = API_TS.read_text(encoding="utf-8")
        start = source.index("export function sectorSlug")
        end = source.index("\n}", start) + 2
        body = source[start:end].replace("export function", "function")

        script = f"""
{body}
const names = {json.dumps(NAMES, ensure_ascii=False)};
console.log(JSON.stringify(names.map(sectorSlug)));
"""
        # 타입 주석이 남아 있으므로 node 의 타입 제거 모드로 실행합니다.
        with tempfile.NamedTemporaryFile("w", suffix=".mts", encoding="utf-8",
                                         delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run(
                [node, "--experimental-strip-types", path],
                capture_output=True, text=True, timeout=60,
            )
        finally:
            Path(path).unlink(missing_ok=True)
        if proc.returncode != 0 and "strip-types" in proc.stderr:
            pytest.skip("이 node 는 타입 제거를 지원하지 않습니다")
        assert proc.returncode == 0, f"node 실행 실패: {proc.stderr[:400]}"
        from_ts = json.loads(proc.stdout)
        from_py = [_slug(n) for n in NAMES]
        assert from_ts == from_py, (
            "프론트엔드와 백엔드의 파일명 규칙이 다릅니다 -- 정적 배포에서 "
            f"분석 버튼이 404 가 됩니다.\n  TS: {from_ts}\n  PY: {from_py}"
        )
