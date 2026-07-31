"""AI 분석 계층·환경설정·자동 갱신.

여기서 지키려는 계약:
    * **AI 호출은 버튼을 눌렀을 때만 나갑니다.** GET 으로 유료 호출이 일어나면
      새로고침 한 번이 과금입니다.
    * **결과는 남습니다.** 같은 질문에 같은 답이 나오지 않으므로 지우면 복구할
      수 없습니다. 실패한 호출도 이유와 함께 남깁니다.
    * **키가 없을 때와 모델 이름이 틀렸을 때가 구분됩니다.** 둘 다 '알 수 없는
      오류'로 뭉개면 사용자가 무엇을 고쳐야 할지 알 수 없습니다.
    * **프롬프트에는 앱이 계산한 숫자만 들어갑니다.** 모델이 기억으로 지어낸
      실적·뉴스를 섞으면 검증 불가능한 서술이 됩니다.
"""

from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest
from conftest import make_panel
from fastapi.testclient import TestClient

from app.ai import prompts
from app.ai.client import AIError, complete
from app.ai.logs import AILogStore
from app.prefs import PreferenceStore
from app.store import Store


# ── 환경설정 저장소 ───────────────────────────────────────────────────────
class TestPreferences:
    def test_defaults_when_no_file(self, tmp_path):
        prefs = PreferenceStore(tmp_path / "p.json").load()
        assert prefs.ai_model
        assert prefs.auto_refresh_enabled is False, "자동 네트워크 호출은 기본 꺼짐"

    def test_partial_save_keeps_other_fields(self, tmp_path):
        st = PreferenceStore(tmp_path / "p.json")
        st.save({"ai_model": "some-model"})
        st.save({"auto_refresh_interval_minutes": 15})
        prefs = st.load()
        assert prefs.ai_model == "some-model"
        assert prefs.auto_refresh_interval_minutes == 15

    def test_env_wins_over_file(self, tmp_path, monkeypatch):
        st = PreferenceStore(tmp_path / "p.json")
        st.save({"ai_model": "from-file"})
        monkeypatch.setenv("AI_MODEL", "from-env")
        assert st.load().ai_model == "from-env"
        assert "ai_model" in st.env_controlled()

    def test_corrupt_file_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text("{ this is not json", encoding="utf-8")
        assert PreferenceStore(path).load().ai_model  # 앱이 뜨지 못하면 안 됩니다

    def test_unknown_market_is_rejected(self, tmp_path):
        st = PreferenceStore(tmp_path / "p.json")
        with pytest.raises(ValueError):
            st.save({"auto_refresh_markets": ["JP"]})

    def test_bad_base_url_is_rejected(self, tmp_path):
        st = PreferenceStore(tmp_path / "p.json")
        with pytest.raises(ValueError):
            st.save({"ai_base_url": "api.example.com"})


# ── 클라이언트 ────────────────────────────────────────────────────────────
def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        request=httpx.Request("POST", "https://x/chat/completions"),
    )


OK_BODY = {
    "model": "test-model",
    "choices": [{"message": {"content": "## 지금 상태\n분석."}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


class TestAIClient:
    def test_missing_key_is_a_settings_problem_not_a_crash(self):
        with pytest.raises(AIError) as e:
            complete(api_key="", base_url="https://x", model="m", system="s", user="u")
        assert e.value.status == 409
        assert "설정" in str(e.value)

    def test_model_name_is_passed_through_untouched(self, monkeypatch):
        seen: dict = {}

        def fake_post(url, **kw):
            seen.update(kw["json"])
            return _response(200, OK_BODY)

        monkeypatch.setattr(httpx, "post", fake_post)
        complete(api_key="k" * 20, base_url="https://x/v1", model="gpt-5.6-luna",
                 system="s", user="u")
        assert seen["model"] == "gpt-5.6-luna", "앱이 모델 이름을 바꾸면 안 됩니다"

    def test_retries_with_legacy_token_param(self, monkeypatch):
        """토큰 상한 파라미터 이름은 모델 세대별로 갈립니다."""
        attempts: list[dict] = []

        def fake_post(url, **kw):
            attempts.append(kw["json"])
            if "max_completion_tokens" in kw["json"]:
                return _response(
                    400,
                    {"error": {"message": "Unsupported parameter: 'max_completion_tokens'"}},
                )
            return _response(200, OK_BODY)

        monkeypatch.setattr(httpx, "post", fake_post)
        out = complete(api_key="k" * 20, base_url="https://x/v1", model="m",
                       system="s", user="u")
        assert out.content.startswith("## 지금 상태")
        assert len(attempts) == 2
        assert "max_tokens" in attempts[1]

    def test_unknown_model_message_reaches_the_user(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: _response(
                404, {"error": {"message": "The model `nope` does not exist"}}
            ),
        )
        with pytest.raises(AIError) as e:
            complete(api_key="k" * 20, base_url="https://x/v1", model="nope",
                     system="s", user="u")
        assert e.value.status == 404
        assert "does not exist" in str(e.value), "공급자 설명을 삼키면 안 됩니다"

    def test_rejected_key_is_401_not_generic(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: _response(401, {"error": {"message": "Incorrect API key"}}),
        )
        with pytest.raises(AIError) as e:
            complete(api_key="k" * 20, base_url="https://x/v1", model="m",
                     system="s", user="u")
        assert e.value.status == 401

    def test_empty_content_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kw: _response(200, {"choices": [{"message": {"content": ""}}]}),
        )
        with pytest.raises(AIError):
            complete(api_key="k" * 20, base_url="https://x/v1", model="m",
                     system="s", user="u")


# ── 프롬프트 ──────────────────────────────────────────────────────────────
class TestPrompts:
    def test_system_forbids_invention_and_point_forecasts(self):
        assert "지어내지" in prompts.SYSTEM
        assert "점 예측" in prompts.SYSTEM
        assert "추천을 하지" in prompts.SYSTEM

    def test_stock_prompt_embeds_the_computed_numbers(self):
        report = {"market": "US", "ticker": "AAA", "name": "A Corp",
                  "indicators": {"rsi_14": 61.5}}
        text = prompts.stock_prompt(report)
        assert "61.5" in text, "근거 숫자가 프롬프트에 없으면 모델이 지어냅니다"
        assert "AAA" in text

    def test_market_prompt_bans_inventing_new_tickers(self):
        text = prompts.market_prompt({"market": "KR", "attention": []})
        assert "새로 추가하지" in text


# ── 로그 저장소 ───────────────────────────────────────────────────────────
@pytest.fixture
def log_store(tmp_path):
    return AILogStore(Store(path=tmp_path / "logs.duckdb"))


class TestAILogs:
    def test_roundtrip_keeps_facts(self, log_store):
        entry_id = log_store.add(
            kind="stock", market="US", subject="AAPL", subject_label="Apple (AAPL)",
            model="m", content="본문", facts={"rsi_14": 61.5},
        )
        got = log_store.get(entry_id)
        assert got.content == "본문"
        assert got.facts["rsi_14"] == 61.5, "그때의 근거 숫자가 남아야 검증이 가능합니다"

    def test_list_returns_preview_not_full_body(self, log_store):
        log_store.add(kind="stock", market="US", subject="A", subject_label=None,
                      model="m", content="가" * 900, facts=None)
        [row] = log_store.list()
        assert len(row.content) < 900
        assert row.facts is None, "목록에 근거 전문을 실으면 응답이 비대해집니다"

    def test_failures_are_recorded(self, log_store):
        log_store.add(kind="market", market="KR", subject="KR", subject_label=None,
                      model="m", content=None, facts=None, error="401 거부됨")
        [row] = log_store.list()
        assert row.error == "401 거부됨"

    def test_newest_first_and_filters(self, log_store):
        log_store.add(kind="stock", market="US", subject="A", subject_label=None,
                      model="m", content="1", facts=None)
        log_store.add(kind="sector", market="KR", subject="반도체", subject_label=None,
                      model="m", content="2", facts=None)
        assert [r.kind for r in log_store.list()][0] == "sector"
        assert len(log_store.list(kind="stock")) == 1
        assert len(log_store.list(market="KR")) == 1

    def test_delete(self, log_store):
        entry_id = log_store.add(kind="stock", market="US", subject="A",
                                 subject_label=None, model="m", content="x", facts=None)
        assert log_store.delete(entry_id) is True
        assert log_store.delete(entry_id) is False
        assert log_store.get(entry_id) is None


# ── AI API ────────────────────────────────────────────────────────────────
@pytest.fixture
def ai_client(tmp_path, monkeypatch):
    st = Store(path=tmp_path / "ai.duckdb")
    panel = make_panel(n_days=200, n_tickers=4, seed=3)
    panel["name"] = panel["ticker"] + " Inc"
    panel["sector"] = "Technology"
    panel["industry"] = "Semiconductors"
    st.upsert_prices("US", panel)

    import app.ai.logs as logs_mod
    import app.store as store_mod

    monkeypatch.setattr(store_mod, "_store", st)
    monkeypatch.setattr(logs_mod, "_logs", AILogStore(st))

    import app.prefs as prefs_mod

    monkeypatch.setattr(prefs_mod, "_store", PreferenceStore(tmp_path / "p.json"))

    from app.main import app

    return TestClient(app), st


class TestAIApi:
    def test_status_reports_missing_key_with_instructions(self, ai_client, monkeypatch):
        client, _ = ai_client
        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "")
        body = client.get("/api/ai/status").json()
        assert body["configured"] is False
        assert "설정" in body["message"]
        assert body["disclaimer"]

    def test_analysis_without_key_is_409_not_500(self, ai_client, monkeypatch):
        client, _ = ai_client
        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "")
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        resp = client.post(f"/api/ai/analyze/stock/US/{ticker}")
        assert resp.status_code == 409
        assert "키" in resp.json()["detail"]

    def test_get_does_not_trigger_a_paid_call(self, ai_client, monkeypatch):
        """유료 호출이 GET 이면 새로고침만으로 과금됩니다."""
        client, _ = ai_client
        called = {"n": 0}
        monkeypatch.setattr(
            "app.ai.service.complete",
            lambda **kw: called.__setitem__("n", called["n"] + 1),
        )
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        assert client.get(f"/api/ai/analyze/stock/US/{ticker}").status_code == 405
        assert called["n"] == 0

    def test_successful_analysis_is_logged_with_its_facts(self, ai_client, monkeypatch):
        client, _ = ai_client
        from app.ai.client import AIResponse

        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "k" * 20)
        monkeypatch.setattr(
            "app.ai.service.complete",
            lambda **kw: AIResponse("## 지금 상태\n서술", "test-model", 11, 22, 1234),
        )
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        body = client.post(f"/api/ai/analyze/stock/US/{ticker}").json()
        assert body["content"].startswith("## 지금 상태")
        assert body["model"] == "test-model"

        detail = client.get(f"/api/ai/logs/{body['id']}").json()
        assert detail["facts"]["ticker"] == ticker, "근거 숫자가 함께 남아야 합니다"
        assert client.get("/api/ai/logs").json()[0]["id"] == body["id"]

    def test_provider_failure_is_logged_and_surfaced(self, ai_client, monkeypatch):
        client, _ = ai_client

        def boom(**kw):
            raise AIError("The model `x` does not exist", status=404)

        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "k" * 20)
        monkeypatch.setattr("app.ai.service.complete", boom)
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        resp = client.post(f"/api/ai/analyze/stock/US/{ticker}")
        assert resp.status_code == 404
        assert "does not exist" in resp.json()["detail"]

        logs = client.get("/api/ai/logs").json()
        assert logs[0]["error"], "실패한 호출도 남아야 원인을 추적할 수 있습니다"

    def test_prompt_only_contains_app_computed_numbers(self, ai_client, monkeypatch):
        """모델에게 넘어가는 사용자 프롬프트에 앱 밖 정보가 섞이면 안 됩니다."""
        client, _ = ai_client
        captured: dict = {}

        from app.ai.client import AIResponse

        def capture(**kw):
            captured.update(kw)
            return AIResponse("## 지금 상태\n x", "m", None, None, 1)

        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "k" * 20)
        monkeypatch.setattr("app.ai.service.complete", capture)
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        client.post(f"/api/ai/analyze/stock/US/{ticker}")

        payload = captured["user"]
        blob = payload[payload.index("```json") + 7 : payload.rindex("```")]
        facts = json.loads(blob)
        assert set(facts.keys()) == {"stock"}
        assert facts["stock"]["ticker"] == ticker

    def test_delete_log(self, ai_client, monkeypatch):
        client, _ = ai_client
        from app.ai.client import AIResponse

        monkeypatch.setattr("app.ai.service.get_credential", lambda name: "k" * 20)
        monkeypatch.setattr(
            "app.ai.service.complete", lambda **kw: AIResponse("본문", "m", 1, 1, 1)
        )
        ticker = client.get("/api/universe/US").json()[0]["ticker"]
        entry_id = client.post(f"/api/ai/analyze/stock/US/{ticker}").json()["id"]
        assert client.delete(f"/api/ai/logs/{entry_id}").status_code == 200
        assert client.get(f"/api/ai/logs/{entry_id}").status_code == 404

    def test_unknown_market_is_404(self, ai_client):
        client, _ = ai_client
        assert client.post("/api/ai/analyze/market/JP").status_code == 404


# ── 자동 갱신 ─────────────────────────────────────────────────────────────
class TestRefresh:
    def test_status_reflects_preferences(self, ai_client, monkeypatch):
        client, _ = ai_client
        client.put(
            "/api/settings/preferences",
            json={"auto_refresh_enabled": True, "auto_refresh_interval_minutes": 30,
                  "auto_refresh_markets": ["US"]},
        )
        body = client.get("/api/refresh/status").json()
        assert body["enabled"] is True
        assert body["interval_minutes"] == 30
        assert body["markets"] == ["US"]

    def test_incremental_refresh_only_asks_for_recent_days(self, ai_client, monkeypatch):
        """매 주기마다 10년치를 다시 받으면 무료 소스는 조용히 차단합니다."""
        client, store = ai_client
        seen: dict = {}

        def fake_ingest(*args, **kw):
            seen.update(kw)
            from app.ingest.pipeline import IngestResult

            return IngestResult("US", "prices", 0, 0, None, None, ["없음"])

        monkeypatch.setattr("app.ingest.pipeline.ingest_us_prices", fake_ingest)
        client.post("/api/refresh/run?market=US")

        last = pd.Timestamp(store.prices("US")["date"].max()).date()
        assert seen["since"] is not None
        assert seen["since"] < last, "겹치는 구간이 없으면 정정·휴장 구멍이 남습니다"
        assert seen["with_classification"] is False

    def test_first_ever_refresh_pulls_full_history(self, ai_client, monkeypatch):
        client, _ = ai_client
        seen: dict = {}

        def fake_ingest(*args, **kw):
            seen.update(kw)
            from app.ingest.pipeline import IngestResult

            return IngestResult("KR", "prices", 0, 0, None, None, [])

        monkeypatch.setattr("app.ingest.pipeline.ingest_us_prices", fake_ingest)
        # KR 은 이 fixture 에 데이터가 없습니다 -- 첫 수집 경로입니다.
        import app.refresh as refresh_mod

        monkeypatch.setattr(refresh_mod, "last_stored_date", lambda m: None)
        client.post("/api/refresh/run?market=US")
        assert seen["since"] is None
        assert seen["with_classification"] is True, "첫 수집에서는 섹터도 받아야 합니다"

    def test_failure_is_reported_not_raised(self, ai_client, monkeypatch):
        client, _ = ai_client

        def boom(*args, **kw):
            raise RuntimeError("네트워크 끊김")

        monkeypatch.setattr("app.ingest.pipeline.ingest_us_prices", boom)
        body = client.post("/api/refresh/run?market=US").json()
        assert body[0]["ok"] is False
        assert "네트워크 끊김" in body[0]["detail"]

    def test_refresh_invalidates_forecast_cache(self, ai_client, monkeypatch):
        client, _ = ai_client
        from app.api import analysis as api
        from app.ingest.pipeline import IngestResult

        api._FORECAST_CACHE[("US", "direction", 21)] = (("stamp", 1), object())
        monkeypatch.setattr(
            "app.ingest.pipeline.ingest_us_prices",
            lambda *a, **k: IngestResult(
                "US", "prices", 5, 1, pd.Timestamp("2024-01-01").date(),
                pd.Timestamp("2024-01-05").date(), []
            ),
        )
        client.post("/api/refresh/run?market=US")
        assert ("US", "direction", 21) not in api._FORECAST_CACHE

    def test_unknown_market_is_404(self, ai_client):
        client, _ = ai_client
        assert client.post("/api/refresh/run?market=JP").status_code == 404
