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


class CostModel(BaseSettings):
    """거래비용 모델.

    전부 소수 비율입니다 (0.0015 == 0.15%). 기본값은 '보수적 자리표시자'이며
    실제 세율이 아닙니다 -- 반드시 확인 후 주입하십시오.
    """

    model_config = SettingsConfigDict(env_prefix="COST_", env_file=".env", extra="ignore")

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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    krx_auth_key: str = Field(default="", description="KRX Open API 인증키 (AUTH_KEY 헤더)")
    sec_user_agent: str = Field(
        default="",
        description="SEC EDGAR User-Agent. 'Name email@example.com' 형식의 실제 "
        "연락처가 필요하며, 없으면 SEC 가 요청을 차단합니다.",
    )
    krx_openapi_base: str = "https://data-dbg.krx.co.kr/svc/apis"
    krx_mdc_base: str = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

    krx_daily_call_budget: int = Field(
        default=10_000, description="KRX Open API 일일 호출 한도"
    )

    data_dir: Path = BACKEND_ROOT / "data"
    cache_dir: Path = BACKEND_ROOT / "data" / "cache"
    duckdb_path: Path = BACKEND_ROOT / "data" / "stock.duckdb"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
cost_model = CostModel()
