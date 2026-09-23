import type { NextConfig } from "next";

/**
 * 后端地址只在这里配置一次（同源反向代理）。
 *
 * 浏览器侧永远请求同源的 `/api/v1/*`，由 Next 转发到 FastAPI：
 * 因此不需要 CORS、不需要处理跨域 Cookie，下载链接也不用改写。
 * 生产部署时只改 `BACKEND_ORIGIN` 即可（阶段 6）。
 *
 * 注意：**上传不走这里**（代理层默认限制 10 MB 请求体，而上传上限是 2048 MB），
 * 上传由 `src/app/api/v1/tasks/route.ts` 流式转发。
 */
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  /**
   * 开发模式下允许访问开发资源的来源。
   *
   * Next 16 默认拦截「跨来源」的开发资源请求（HMR、开发态 chunk）。
   * 用 `127.0.0.1` 或局域网 IP 打开时会被判定为跨来源，页面会加载不完整 ——
   * 真实踩过（日志：Blocked cross-origin request to Next.js dev resource /_next/hmr）。
   * 这里把本机常用地址与局域网地址都放行，方便手机连同一 Wi-Fi 测试。
   */
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    "192.168.0.116",
    "*.local",
  ],

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
