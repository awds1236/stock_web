import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "stock_web — 지표 해석과 예측",
  description: "한국·미국 주식 지표 해석과 보정된 확률 예측",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body>
        <div className="layout">
          <aside className="sidebar">
            <h1>stock_web</h1>
            <div className="tag">지표 해석 · 확률 예측</div>
            <nav>
              <Link href="/">개요</Link>
              <Link href="/market">시장</Link>
              <Link href="/watchlist">관찰 목록</Link>
              <Link href="/stocks">종목</Link>
              <Link href="/sectors">섹터</Link>
              <Link href="/forecast">예측</Link>
              <Link href="/ai-logs">AI 기록</Link>
              <Link href="/settings">설정</Link>
              <Link href="/methodology">방법론</Link>
            </nav>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
