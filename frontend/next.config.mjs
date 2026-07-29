/** @type {import('next').NextConfig} */
const nextConfig = {
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
