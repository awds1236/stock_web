"""인증정보 저장소·설정 API 테스트.

가장 중요한 검증 두 가지:
  1. 저장된 값이 API 응답으로 **평문 유출되지 않는가**
  2. 디스크에 **평문으로 남지 않는가**
둘 중 하나라도 뚫리면 .env 대신 앱에 넣은 의미가 사라집니다.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app.credentials import CredentialError, CredentialStore, mask, validate

REAL_KEY = "ABCD1234EFGH5678IJKL"
REAL_UA = "Hong Gildong hong@example.org"


@pytest.fixture
def tmp_store(tmp_path):
    return CredentialStore(
        store_path=tmp_path / "credentials.enc",
        key_path=tmp_path / ".credential_key",
    )


@pytest.fixture
def client(tmp_store, monkeypatch):
    """설정 API 를 임시 저장소에 연결한 테스트 클라이언트."""
    import app.api.settings_router as router_mod
    import app.credentials as cred_mod

    monkeypatch.setattr(cred_mod, "store", tmp_store)
    monkeypatch.setattr(router_mod, "store", tmp_store)
    monkeypatch.delenv("KRX_AUTH_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    from app.main import app

    return TestClient(app)


# ── 저장소 기본 동작 ─────────────────────────────────────────────────────
class TestCredentialStore:
    def test_roundtrip(self, tmp_store):
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        assert tmp_store.get_stored("KRX_AUTH_KEY") == REAL_KEY

    def test_value_is_not_plaintext_on_disk(self, tmp_store):
        """디스크를 직접 열어봐도 키가 보이면 안 됩니다."""
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        raw = tmp_store.store_path.read_bytes()
        assert REAL_KEY.encode() not in raw
        assert b"KRX_AUTH_KEY" not in raw  # 키 이름조차 평문으로 남지 않습니다

    def test_files_are_owner_only(self, tmp_store):
        """다른 사용자 계정이 읽을 수 있으면 암호화 의미가 줄어듭니다."""
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        for path in (tmp_store.store_path, tmp_store.key_path):
            mode = path.stat().st_mode
            assert not mode & (stat.S_IRWXG | stat.S_IRWXO), f"{path} 권한이 개방적"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX 권한 검사는 Windows 미적용")
    def test_loose_permissions_are_rejected(self, tmp_store):
        """권한이 느슨해졌으면 조용히 넘어가지 않아야 합니다."""
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        tmp_store.key_path.chmod(0o644)
        with pytest.raises(CredentialError, match="권한"):
            tmp_store.get_stored("KRX_AUTH_KEY")

    def test_permission_check_is_skipped_on_windows(self, tmp_store, monkeypatch):
        """Windows 에서는 st_mode 가 항상 '개방적'으로 보고되므로 검사를 건너뜁니다.

        건너뛰지 않으면 Windows 사용자는 키를 저장하는 순간부터 모든 조회가
        실패합니다 (실사용에서 발생한 버그의 회귀 테스트).
        """
        import app.credentials as cred_mod

        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        tmp_store.store_path.chmod(0o666)  # Windows 가 보고하는 형태를 재현
        tmp_store.key_path.chmod(0o666)
        monkeypatch.setattr(cred_mod.os, "name", "nt")
        assert tmp_store.get_stored("KRX_AUTH_KEY") == REAL_KEY

    def test_delete_removes_value(self, tmp_store):
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        assert tmp_store.delete("KRX_AUTH_KEY") is True
        assert tmp_store.get_stored("KRX_AUTH_KEY") == ""
        assert tmp_store.delete("KRX_AUTH_KEY") is False

    def test_unmanaged_name_is_rejected(self, tmp_store):
        """임의의 키/값 저장소가 되면 무엇이 비밀인지 추적할 수 없습니다."""
        with pytest.raises(CredentialError, match="관리 대상이 아닌"):
            tmp_store.set("MY_RANDOM_SECRET", "x" * 20)

    def test_corrupted_store_gives_actionable_error(self, tmp_store):
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        tmp_store.store_path.write_bytes(b"garbage that is not fernet")
        with pytest.raises(CredentialError, match="복호화"):
            tmp_store.get_stored("KRX_AUTH_KEY")

    def test_multiple_credentials_coexist(self, tmp_store):
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        tmp_store.set("SEC_USER_AGENT", REAL_UA)
        assert tmp_store.get_stored("KRX_AUTH_KEY") == REAL_KEY
        assert tmp_store.get_stored("SEC_USER_AGENT") == REAL_UA


# ── 환경변수 우선순위 ────────────────────────────────────────────────────
class TestPrecedence:
    def test_env_wins_over_stored(self, tmp_store, monkeypatch):
        """배포 시 주입한 환경변수가 UI 에 남은 옛 값에 덮이면 안 됩니다."""
        tmp_store.set("KRX_AUTH_KEY", "STORED_VALUE_1234")
        monkeypatch.setenv("KRX_AUTH_KEY", "ENV_VALUE_5678")
        assert tmp_store.resolve("KRX_AUTH_KEY") == "ENV_VALUE_5678"

    def test_stored_used_when_env_absent(self, tmp_store, monkeypatch):
        monkeypatch.delenv("KRX_AUTH_KEY", raising=False)
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        assert tmp_store.resolve("KRX_AUTH_KEY") == REAL_KEY

    def test_empty_env_does_not_shadow_stored(self, tmp_store, monkeypatch):
        """빈 환경변수가 저장된 값을 가리면 '왜 안 되지' 상황이 됩니다."""
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)
        monkeypatch.setenv("KRX_AUTH_KEY", "   ")
        assert tmp_store.resolve("KRX_AUTH_KEY") == REAL_KEY

    def test_env_backed_credential_is_not_editable(self, tmp_store, monkeypatch):
        monkeypatch.setenv("KRX_AUTH_KEY", "ENV_VALUE_5678")
        status = tmp_store.status("KRX_AUTH_KEY")
        assert status.source == "env"
        assert status.editable is False


# ── 마스킹 ───────────────────────────────────────────────────────────────
class TestMasking:
    def test_long_key_shows_only_edges(self):
        masked = mask("ABCD1234EFGH5678IJKL")
        assert masked.startswith("ABCD") and masked.endswith("IJKL")
        assert "1234EFGH5678" not in masked

    def test_short_value_is_fully_hidden(self):
        assert mask("short") == "*****"

    def test_email_keeps_domain_only(self):
        masked = mask("Hong Gildong hong@example.org")
        assert masked.endswith("@example.org")
        assert "Gildong" not in masked

    def test_empty_is_empty(self):
        assert mask("") == ""


# ── 형식 검증 ────────────────────────────────────────────────────────────
class TestValidation:
    def test_sec_requires_email(self):
        with pytest.raises(CredentialError, match="이메일"):
            validate("SEC_USER_AGENT", "just-a-scraper/1.0")

    def test_sec_accepts_name_and_email(self):
        validate("SEC_USER_AGENT", REAL_UA)  # 예외 없음

    def test_krx_rejects_too_short(self):
        with pytest.raises(CredentialError, match="짧습니다"):
            validate("KRX_AUTH_KEY", "abc")

    def test_krx_rejects_whitespace(self):
        """복사·붙여넣기 시 딸려오는 공백을 잡습니다."""
        with pytest.raises(CredentialError, match="공백"):
            validate("KRX_AUTH_KEY", "ABCD 1234 EFGH")


# ── 설정 API ─────────────────────────────────────────────────────────────
class TestSettingsApi:
    def test_list_shows_unconfigured_initially(self, client):
        body = client.get("/api/settings/credentials").json()
        names = {c["name"] for c in body["credentials"]}
        assert names == {"KRX_AUTH_KEY", "SEC_USER_AGENT"}
        assert all(c["configured"] is False for c in body["credentials"])
        assert body["warning"]  # 배포 경고가 응답에 포함되어야 합니다

    def test_put_then_list_shows_masked_only(self, client):
        """저장된 키가 API 응답에 평문으로 나오면 안 됩니다."""
        resp = client.put(
            "/api/settings/credentials/KRX_AUTH_KEY", json={"value": REAL_KEY}
        )
        assert resp.status_code == 200
        assert REAL_KEY not in resp.text

        listing = client.get("/api/settings/credentials")
        assert REAL_KEY not in listing.text, "전체 응답 어디에도 평문 키가 없어야 합니다"
        krx = next(
            c for c in listing.json()["credentials"] if c["name"] == "KRX_AUTH_KEY"
        )
        assert krx["configured"] is True
        assert krx["source"] == "stored"

    def test_put_rejects_invalid_format(self, client):
        resp = client.put(
            "/api/settings/credentials/SEC_USER_AGENT", json={"value": "no-email-here"}
        )
        assert resp.status_code == 400
        assert "이메일" in resp.json()["detail"]

    def test_put_rejects_unknown_credential(self, client):
        resp = client.put(
            "/api/settings/credentials/SOME_OTHER_KEY", json={"value": "x" * 20}
        )
        assert resp.status_code == 404

    def test_put_conflicts_when_env_takes_precedence(self, client, monkeypatch):
        """환경변수가 이기는데 저장을 허용하면 '고쳤는데 안 바뀐다'가 됩니다."""
        monkeypatch.setenv("KRX_AUTH_KEY", "ENV_VALUE_5678")
        resp = client.put(
            "/api/settings/credentials/KRX_AUTH_KEY", json={"value": REAL_KEY}
        )
        assert resp.status_code == 409
        assert "환경변수" in resp.json()["detail"]

    def test_delete_clears_credential(self, client):
        client.put("/api/settings/credentials/KRX_AUTH_KEY", json={"value": REAL_KEY})
        resp = client.delete("/api/settings/credentials/KRX_AUTH_KEY")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False

    def test_empty_value_is_rejected(self, client):
        resp = client.put(
            "/api/settings/credentials/KRX_AUTH_KEY", json={"value": ""}
        )
        assert resp.status_code == 422  # pydantic min_length

    def test_markets_endpoint_carries_comparability_warning(self, client):
        """두 시장을 나란히 렌더링할 때 같은 신뢰도로 읽히지 않도록."""
        body = client.get("/api/markets").json()
        assert body["comparability_warning"]
        assert all(m["flow_caveat"] for m in body["markets"])


# ── provider 연동 ────────────────────────────────────────────────────────
class TestProviderIntegration:
    def test_krx_client_reads_from_store(self, tmp_store, monkeypatch):
        import app.credentials as cred_mod
        from app.providers.krx_openapi import KrxOpenApiClient

        monkeypatch.delenv("KRX_AUTH_KEY", raising=False)
        monkeypatch.setattr(cred_mod, "store", tmp_store)
        tmp_store.set("KRX_AUTH_KEY", REAL_KEY)

        assert KrxOpenApiClient().auth_key == REAL_KEY

    def test_sec_client_reads_from_store(self, tmp_store, monkeypatch):
        import app.credentials as cred_mod
        from app.providers.sec_edgar import SecEdgarClient

        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        monkeypatch.setattr(cred_mod, "store", tmp_store)
        tmp_store.set("SEC_USER_AGENT", REAL_UA)

        assert SecEdgarClient().user_agent == REAL_UA

    def test_krx_error_points_to_settings_screen(self, tmp_store, monkeypatch):
        """키가 없을 때의 안내가 실제 입력 위치를 가리켜야 합니다."""
        import app.credentials as cred_mod
        from app.providers.base import ProviderError
        from app.providers.krx_openapi import ENDPOINTS, KrxOpenApiClient

        monkeypatch.delenv("KRX_AUTH_KEY", raising=False)
        monkeypatch.setattr(cred_mod, "store", tmp_store)

        with pytest.raises(ProviderError, match="설정 화면"):
            KrxOpenApiClient().call(ENDPOINTS[0], {"basDd": "20260728"})


def test_store_file_never_contains_json_plaintext(tmp_path):
    """암호화가 실제로 걸렸는지 -- JSON 구조가 보이면 안 됩니다."""
    s = CredentialStore(
        store_path=tmp_path / "c.enc", key_path=tmp_path / ".k"
    )
    s.set("KRX_AUTH_KEY", REAL_KEY)
    raw = s.store_path.read_bytes()
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        json.loads(raw.decode("utf-8"))
