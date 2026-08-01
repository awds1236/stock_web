# 백엔드 컨테이너 -- **전 기능 동적 모드**로 가는 길.
#
# GitHub Pages 는 정적 파일만 서빙합니다. 스냅샷 + 브라우저 직접 시세만으로도
# 가격은 살아 움직이지만, 다음은 백엔드 없이 불가능합니다:
#
#     주가 자동 수집 · 임의 기간 예측 · AI 서술 분석 · 인증정보 저장
#
# 이 이미지를 아무 컨테이너 호스트에 올리고, 프론트엔드 설정 화면에서 그
# 주소를 연결하면 됩니다. **재빌드도 재배포도 필요 없습니다** -- 주소는
# 브라우저에 저장됩니다.
#
#   docker build -t stock-web-backend ./backend
#   docker run -p 8000:8000 \
#     -e CORS_ORIGINS=https://<계정>.github.io \
#     -v stock-web-data:/data \
#     stock-web-backend

FROM python:3.11-slim

# 빌드 도구 없이 설치되도록 휠을 우선합니다. 소스 빌드가 필요한 패키지가
# 생기면 이미지가 수백 MB 커지고 빌드가 몇 분 늘어납니다.
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 의존성만 먼저 설치해 레이어를 캐시합니다. 코드가 바뀔 때마다 pandas 를
# 다시 받으면 배포가 느려집니다.
COPY pyproject.toml README.md* ./
COPY app ./app
RUN pip install .

# 데이터는 볼륨에 둡니다. 컨테이너를 다시 만들 때마다 10년치를 다시 받으면
# 무료 소스가 차단으로 응답합니다.
ENV DATA_DIR=/data \
    CACHE_DIR=/data/cache \
    DUCKDB_PATH=/data/stock.duckdb
VOLUME ["/data"]

# 인증정보 암호화 키도 /data 에 생성되므로, 볼륨이 사라지면 저장한 키를
# 복호화할 수 없게 됩니다 -- 그때는 설정 화면에서 다시 입력하면 됩니다.

EXPOSE 8000

# 워커 하나로 고정합니다. DuckDB 는 파일당 쓰기 프로세스를 하나만 허용하므로,
# 워커를 늘리면 두 번째 프로세스가 락 오류로 죽습니다.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
