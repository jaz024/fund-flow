import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "资金脉络｜A股板块、个股异动与策略实验室",
  description: "沪深京A股板块资金流回放、个股分钟异动、排名、趋势及本地观察性策略模拟。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body data-fund-flow-version="5">{children}</body>
    </html>
  );
}
