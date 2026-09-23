import type { NextConfig } from "next";

/**
 * 后端地址只在这里配置一次（同源反向代理）。
 *
 * 浏览器侧永远请求同源的 `/api/v1/*`，由 Next 转发到 FastAPI：
 * 因此不需要 CORS、不需要处理跨域 Cookie，下载链接也不用改写。
 * 生产部署时只改 `BACKEND_ORIGIN` 即可（阶段 6）。
 */
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
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
