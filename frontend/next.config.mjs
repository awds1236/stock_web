/**
 * 두 가지 빌드 모드:
 *
 *  - 기본 (로컬): /api/* 를 FastAPI 백엔드로 프록시. 수집·설정·임의 기간 예측
 *    전부 동작.
 *  - 정적 (STATIC_EXPORT=1, GitHub Pages): output:'export' 로 순수 정적 사이트
 *    생성. 백엔드가 없으므로 데이터는 CI 가 만들어 둔 /data/*.json 스냅샷.
 *    rewrites 는 export 모드에서 지원되지 않으므로 정의 자체를 뺍니다.
 *
 * basePath: GitHub Pages 프로젝트 사이트는 https://<user>.github.io/<repo>/
 * 아래에 서빙되므로, CI 가 NEXT_PUBLIC_BASE_PATH=/<repo> 를 주입합니다.
 */
const isStatic = process.env.STATIC_EXPORT === "1";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** @type {import('next').NextConfig} */
const nextConfig = isStatic
  ? {
      output: "export",
      basePath,
      trailingSlash: true, // Pages 가 디렉터리 index.html 을 찾도록
      images: { unoptimized: true },
    }
  : {
      async rewrites() {
        // 프론트엔드에서 /api/* 호출을 백엔드로 전달합니다. 이렇게 하면 브라우저가
        // 동일 출처로 호출하므로 CORS 설정에 의존하지 않습니다.
        return [
          {
            source: "/api/:path*",
            destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*`,
          },
        ];
      },
    };

export default nextConfig;
