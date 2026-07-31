"""OpenAI 호환 채팅 완성 클라이언트.

SDK 를 쓰지 않고 httpx 로 직접 호출하는 이유:

    이 앱은 이미 httpx 에 의존하고 있고, 필요한 것은 엔드포인트 하나입니다.
    SDK 를 넣으면 모델 파라미터 규격이 바뀔 때마다 버전을 따라가야 하는데,
    여기서 원하는 동작은 정반대입니다 -- **모델 이름을 그대로 흘려보내고,
    공급자의 오류 메시지를 사용자에게 그대로 보여주는 것**입니다. 그래야
    "그 모델은 존재하지 않는다"는 답을 앱이 가로채 '알 수 없는 오류'로
    바꿔버리는 일이 없습니다.

모델 이름은 하드코딩하지 않습니다. 설정 화면에서 입력한 문자열을 그대로
보내며, 유효성은 공급자가 판정합니다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


class AIError(RuntimeError):
    """AI 호출 실패. 메시지는 사용자에게 그대로 보여줄 수 있어야 합니다."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AIResponse:
    content: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int


def complete(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    max_output_tokens: int = 2000,
    timeout: float = 120.0,
) -> AIResponse:
    """채팅 완성 1회 호출.

    토큰 상한 파라미터 이름이 모델 세대별로 갈립니다(`max_tokens` ->
    `max_completion_tokens`). 어느 쪽이 맞는지는 모델 이름만 봐서는 알 수
    없으므로, 신형 이름으로 먼저 보내고 공급자가 거부하면 구형 이름으로 한 번
    더 시도합니다. 이 재시도가 없으면 사용자는 모델을 바꿀 때마다 원인을 알 수
    없는 400 을 보게 됩니다.
    """
    if not api_key:
        raise AIError(
            "OpenAI API 키가 설정되지 않았습니다. 설정 화면에서 입력하십시오.",
            status=409,
        )

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": max_output_tokens,
    }

    started = time.monotonic()
    body = _post(base_url, api_key, payload, timeout)
    if body is None:
        payload.pop("max_completion_tokens")
        payload["max_tokens"] = max_output_tokens
        body = _post(base_url, api_key, payload, timeout, retry_on_param=False)
    duration_ms = int((time.monotonic() - started) * 1000)

    content = _extract_text(body)
    if not content.strip():
        raise AIError(
            "모델이 빈 응답을 반환했습니다. 출력 토큰 상한을 늘리거나 다른 "
            "모델을 지정해 보십시오."
        )
    usage = body.get("usage") or {}
    return AIResponse(
        content=content.strip(),
        model=str(body.get("model") or model),
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        duration_ms=duration_ms,
    )


def _post(
    base_url: str,
    api_key: str,
    payload: dict,
    timeout: float,
    *,
    retry_on_param: bool = True,
) -> dict | None:
    """성공하면 body, 파라미터 이름 문제로 400 이면 None (호출자가 재시도)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise AIError(
            f"AI 응답이 {timeout:.0f}초 안에 오지 않았습니다. 설정에서 제한 시간을 "
            "늘리거나 더 빠른 모델을 지정하십시오."
        ) from exc
    except httpx.HTTPError as exc:
        raise AIError(f"AI 엔드포인트에 연결할 수 없습니다: {exc}") from exc

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            raise AIError("AI 응답을 JSON 으로 해석할 수 없습니다.") from exc

    detail = _error_detail(resp)
    if (
        retry_on_param
        and resp.status_code == 400
        and "max_completion_tokens" in detail
    ):
        return None  # 구형 파라미터 이름으로 재시도

    if resp.status_code == 401:
        raise AIError(f"API 키가 거부되었습니다 (401). {detail}", status=401)
    if resp.status_code == 404:
        raise AIError(
            f"모델 또는 엔드포인트를 찾을 수 없습니다 (404). 설정의 모델 이름을 "
            f"확인하십시오. 공급자 응답: {detail}",
            status=404,
        )
    if resp.status_code == 429:
        raise AIError(f"호출 한도에 걸렸습니다 (429). {detail}", status=429)
    raise AIError(f"AI 호출 실패 (HTTP {resp.status_code}). {detail}",
                  status=resp.status_code)


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:400]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:400]
    return str(err or body)[:400]


def _extract_text(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # 일부 호환 엔드포인트는 content 를 블록 배열로 돌려줍니다.
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
