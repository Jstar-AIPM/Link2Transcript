/**
 * 后端地址（只在服务端使用）。
 *
 * 浏览器永远只访问同源地址；真正的后端地址由服务端决定，
 * 因此生产部署只改一个环境变量（也不存在 CORS 问题）。
 */
export function backendOrigin(): string {
  return process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
}
