import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "资金脉络｜A股板块资金流向",
  description: "沪深京A股行业与概念板块资金净流五分钟回放、排名及三个月趋势。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
