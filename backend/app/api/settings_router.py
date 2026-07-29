"""설정 API -- 앱 안에서 인증정보를 입력·삭제합니다.

설계 원칙:
  * **저장된 값은 절대 평문으로 응답하지 않습니다.** 설정 화면은 '키가 들어
    있는지'만 확인하면 되고, 전체 값을 내보내면 파일 밖으로 뺀 의미가 없습니다.
  * 환경변수로 설정된 항목은 `editable=false` 로 표시합니다. 환경변수가
    우선하므로 UI 에서 고쳐도 반영되지 않는데, 그것을 숨기면 사용자가 왜 값이
    안 바뀌는지 알 수 없습니다.
  * 저장 전에 형식을 검증합니다. 잘못된 값을 넣어두면 나중에 호출이 실패할 때
    원인이 인증정보인지 네트워크인지 구분하기 어렵습니다.

*** 배포 시 경고 ***
    이 라우터는 인증이 없습니다. 로컬 단일 사용자 도구를 전제로 합니다.
    외부에 노출된 서버에 그대로 올리면 **누구나 인증정보를 입력·삭제할 수 있는
    창구**가 됩니다. 다중 사용자로 배포하려면 인증을 먼저 붙이십시오.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.credentials import MANAGED, CredentialError, store

router = APIRouter(prefix="/api/settings", tags=["settings"])


class CredentialIn(BaseModel):
    value: str = Field(min_length=1, description="인증정보 값 (저장 후 조회 불가)")


class CredentialOut(BaseModel):
    name: str
    label: str
    help: str
    signup_url: str
    configured: bool
    source: str  # env | stored | none
    masked: str
    editable: bool


class CredentialListOut(BaseModel):
    credentials: list[CredentialOut]
    warning: str


DEPLOY_WARNING = (
    "이 설정 API 에는 인증이 없습니다. 로컬에서만 사용하고, 외부에 노출된 "
    "서버에 배포할 때는 인증을 붙이거나 환경변수 주입 방식으로 전환하십시오."
)


@router.get("/credentials", response_model=CredentialListOut)
def list_credentials() -> CredentialListOut:
    """인증정보 설정 상태. 값은 마스킹되어 반환됩니다."""
    try:
        statuses = store.status_all()
    except CredentialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CredentialListOut(
        credentials=[CredentialOut(**vars(s)) for s in statuses],
        warning=DEPLOY_WARNING,
    )


@router.put("/credentials/{name}", response_model=CredentialOut)
def set_credential(name: str, body: CredentialIn) -> CredentialOut:
    if name not in MANAGED:
        raise HTTPException(status_code=404, detail=f"알 수 없는 인증정보: {name}")

    status = store.status(name)
    if not status.editable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{name} 은(는) 환경변수로 설정되어 있어 환경변수가 우선합니다. "
                "여기서 저장해도 반영되지 않습니다. 환경변수를 해제하거나 "
                "환경변수 쪽 값을 수정하십시오."
            ),
        )

    try:
        store.set(name, body.value)
    except CredentialError as exc:
        # 형식 오류는 사용자 입력 문제이므로 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CredentialOut(**vars(store.status(name)))


@router.delete("/credentials/{name}", response_model=CredentialOut)
def delete_credential(name: str) -> CredentialOut:
    if name not in MANAGED:
        raise HTTPException(status_code=404, detail=f"알 수 없는 인증정보: {name}")
    try:
        store.delete(name)
    except CredentialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return CredentialOut(**vars(store.status(name)))
