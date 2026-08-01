"""애플리케이션 설정.

거래비용 관련 값은 **의도적으로 하드코딩하지 않습니다**. 한국 증권거래세·농어촌
특별세율은 최근 수년간 반복적으로 바뀌었고, 낡은 세율이 코드에 박혀 있으면
백테스트 결과가 조용히 틀어집니다. 운영자가 구현 시점의 실제 세율을 확인해
환경변수로 주입하고, 리포트에 그 값이 함께 기록되도록 합니다.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# .env 는 **절대경로**로 지정합니다. 상대경로("...env")로 두면 현재 작업 디렉터리
# 기준으로 찾기 때문에, backend/ 밖에서 실행하면 키를 넣어두고도 조용히 빈 값이
# 되어 "인증키가 설정되지 않았습니다" 라는 엉뚱한 오류를 보게 됩니다.
ENV_FILE = BACKEND_ROOT / ".env"

# 로컬 개발 프론트엔드는 항상 허용합니다. 이걸 빼면 개발자가 매번 환경변수를
# 세팅해야 하고, 그 과정에서 `*` 를 넣고 싶어집니다.
LOCAL_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


class CostModel(BaseSettings):
    """거래비용 모델.

    전부 소수 비율입니다 (0.0015 == 0.15%). 기본값은 '보수적 자리표시자'이며
    실제 세율이 아닙니다 -- 반드시 확인 후 주입하십시오.
    """

    model_config = SettingsConfigDict(
        env_prefix="COST_", env_file=ENV_FILE, extra="ignore"
    )

    sell_tax: float = Field(
        default=0.0015,
        description="매도 시 증권거래세 + 농어촌특별세 합계. 시장(KOSPI/KOSDAQ)과 "
        "연도에 따라 다르므로 반드시 확인해 주입할 것.",
    )
    commission: float = Field(default=0.00015, description="매수/매도 각각의 위탁수수료율")
    slippage: float = Field(
        default=0.001,
        description="체결 슬리피지. 시가 체결을 가정해도 호가 스프레드와 충격비용이 "
        "존재하므로 0으로 두면 안 됩니다.",
    )
    max_participation: float = Field(
        default=0.01,
        description="일 거래대금 대비 최대 체결 비율. 이 제약이 없으면 소형주에서 "
        "체결 불가능한 수익률이 생성됩니다.",
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # 인증정보(KRX 인증키, SEC 연락처)는 여기 두지 않습니다. 앱 설정 화면에서
    # 입력받아 암호화 저장소(app/credentials.py)에 보관하며, 배포 시에는 동명의
    # 환경변수가 우선합니다. 이 파일에 필드를 다시 만들면 값이 두 곳에 생겨
    # 어느 쪽이 쓰이는지 추적이 어려워집니다.
    krx_openapi_base: str = "https://data-dbg.krx.co.kr/svc/apis"
    krx_mdc_base: str = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

    krx_daily_call_budget: int = Field(
        default=10_000, description="KRX Open API 일일 호출 한도"
    )

    data_dir: Path = BACKEND_ROOT / "data"
    cache_dir: Path = BACKEND_ROOT / "data" / "cache"
    duckdb_path: Path = BACKEND_ROOT / "data" / "stock.duckdb"

    cors_origins: str = Field(
        default="",
        description="쉼표로 구분한 추가 허용 출처. 정적 배포(GitHub Pages)에서 이 "
        "백엔드를 호출하려면 그 출처를 여기에 넣어야 합니다. 예: "
        "https://user.github.io  ***`*` 를 넣지 마십시오*** -- 이 API 에는 "
        "인증정보 입력 엔드포인트가 있어, 임의의 사이트가 사용자의 브라우저를 "
        "통해 호출할 수 있게 됩니다.",
    )

    def cors_origin_list(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        return [*LOCAL_ORIGINS, *extra]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
cost_model = CostModel()
