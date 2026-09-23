import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";

import "./globals.css";

// 字体自托管（构建期打包，不请求外部 CDN）；Inter 只覆盖拉丁与数字，中文字形走系统字体
// 与「03 AI 用户洞察分析器」使用同一套字体，保证系列感
const inter = localFont({
  src: "../../fonts/inter-variable.woff2",
  variable: "--font-inter",
  weight: "100 900",
  display: "swap",
});

const jetbrainsMono = localFont({
  src: "../../fonts/jetbrains-mono-variable.woff2",
  variable: "--font-mono-jb",
  weight: "100 800",
  display: "swap",
});

export const metadata: Metadata = {
  title: "逐字稿提取器",
  description: "上传音频、视频或粘贴 B 站链接，生成可阅读、可下载的中文逐字稿。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
