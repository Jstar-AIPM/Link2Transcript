/**
 * 上传接口的**流式**反向代理。
 *
 * 为什么需要单独写一个 route handler，而不是交给 next.config 的 rewrites：
 * Next 的代理解析会把请求体读进内存以做大小校验，**默认上限 10 MB**，
 * 而本项目的上传上限是 2048 MB —— 直接走 rewrites 会在 >10 MB 的文件上
 * 报 500（真实踩到过：5.9 MB 的测试文件能过，13 MB 的就失败）。
 *
 * 这里用 `request.body` 流式转发：不缓冲、不占内存，
 * 与后端「分块落盘」的行为配合，2 GB 文件也不会把前端进程撑爆。
 * 其余接口（JSON、体积很小）继续由 rewrites 代理。
 */
import { backendOrigin } from "@/lib/server/backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("multipart/form-data")) {
    // 只做这一条判断；其余校验一律交给后端（前端不复制业务规则）
    return Response.json(
      { error: { code: "INVALID_REQUEST", message: "请以表单方式上传文件" } },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${backendOrigin()}/api/v1/tasks`, {
      method: "POST",
      headers: { "content-type": contentType },
      body: request.body,
      // 流式请求体在 Node 的 fetch 下必须声明 half duplex
      duplex: "half",
    } as RequestInit & { duplex: "half" });
  } catch {
    return Response.json(
      {
        error: {
          code: "NETWORK_ERROR",
          message: "网络连接失败，请检查服务是否已启动后重试",
        },
      },
      { status: 502 },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
