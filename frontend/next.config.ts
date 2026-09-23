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

/**
 * 开发模式下允许访问开发资源的来源。
 *
 * Next 16 默认拦截「跨来源」的开发资源请求（HMR、开发态 chunk）。
 * 用 `127.0.0.1` 或局域网 IP 打开时会被判定为跨来源，页面会加载不完整。
 *
 * 默认放行本机地址；用手机/其它设备调试时，把自己的局域网地址加进
 * 环境变量 `ALLOWED_DEV_ORIGINS`（逗号分隔）即可，不需要改代码：
 *   ALLOWED_DEV_ORIGINS=192.168.1.23,my-mac.local npm run dev
 */
function allowedDevOrigins(): string[] {
  const extra = (process.env.ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return ["127.0.0.1", "localhost", "*.local", ...extra];
}

const nextConfig: NextConfig = {
  allowedDevOrigins: allowedDevOrigins(),

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
