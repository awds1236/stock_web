"""FastAPI 애플리케이션.

현재는 설정(인증정보) API 만 노출합니다. 데이터 수집·시그널·백테스트 라우터는
Phase 1 이후에 붙습니다 -- 검증되지 않은 시그널을 API 로 내보내지 않는다는
원칙 때문입니다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.analysis import router as analysis_router
from app.api.settings_router import router as settings_router
from app.config import settings
from app.markets import MARKETS
from app.store import StoreLocked


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    yield


app = FastAPI(
    title="stock_web",
    description="한국·미국 주식 수급·지표 분석 (검증된 범위 안에서만 제시)",
    version="0.1.0",
    lifespan=lifespan,
)

# 로컬 개발용 프론트엔드(Next.js)만 허용합니다. 와일드카드로 열면 임의의
# 사이트가 브라우저를 통해 설정 API 를 호출할 수 있습니다 -- 인증정보를 다루는
# 엔드포인트가 있으므로 특히 위험합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "PUT", "DELETE", "POST"],
    allow_headers=["*"],
)

app.include_router(settings_router)
app.include_router(analysis_router)


@app.exception_handler(StoreLocked)
async def _store_locked(_request: Request, exc: StoreLocked) -> JSONResponse:
    """DB 락은 500 이 아니라 '지금은 불가, 이렇게 하면 됨' 상태입니다.

    불투명한 500 을 그대로 내보내면 사용자는 앱이 고장난 줄 압니다. 실제로는
    수집 스크립트를 종료하기만 하면 되는 상황입니다.
    """
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/markets")
def list_markets() -> dict:
    """지원 시장과 **각 시장의 데이터 한계**.

    한계를 API 에 함께 실어 보내는 이유: 프론트엔드가 두 시장을 나란히
    렌더링할 때 사용자가 같은 신뢰도로 읽지 않도록 하기 위해서입니다.
    """
    return {
        "markets": [
            {
                "code": m.code,
                "name": m.name,
                "currency": m.currency,
                "flow_kind": m.flow_kind,
                "flow_label": m.flow_label,
                "flow_caveat": m.flow_caveat,
            }
            for m in MARKETS.values()
        ],
        "comparability_warning": (
            "한국의 투자자별 수급과 미국의 내부자 매매는 서로 다른 것을 측정합니다. "
            "두 시장의 시그널을 나란히 놓고 우열을 비교하지 마십시오."
        ),
    }
