/**
 * 上传代理的契约测试。
 *
 * 背景（真实踩过的坑）：Next 的 rewrites 代理默认把请求体读进内存并限制 10 MB，
 * 而本项目上传上限是 2048 MB —— 13 MB 的文件就会 500。
 * 因此上传单独走 `app/api/v1/tasks/route.ts` 的**流式**转发。
 * 这个测试锁住它的契约，防止以后有人「顺手删掉」退回 rewrites。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/v1/tasks/route";

function multipartRequest(): Request {
  return new Request("http://localhost/api/v1/tasks", {
    method: "POST",
    headers: { "content-type": "multipart/form-data; boundary=----abc" },
    body: "------abc--",
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("上传代理", () => {
  it("流式转发给后端，并原样回传状态与响应体", async () => {
    const upstreamBody = JSON.stringify({ task_id: "t-9", status: "pending" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(upstreamBody, {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(multipartRequest());

    expect(response.status).toBe(202);
    await expect(response.json()).resolves.toEqual({ task_id: "t-9", status: "pending" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/tasks");
    expect(init.method).toBe("POST");
    // 关键：请求体是流而不是缓冲好的字符串/字节，才能承载 2 GB 上传
    expect(init.body).not.toBeInstanceOf(Uint8Array);
    expect((init as RequestInit & { duplex?: string }).duplex).toBe("half");
  });

  it("后端不可用时返回中文提示（而不是把异常抛给浏览器）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const response = await POST(multipartRequest());

    expect(response.status).toBe(502);
    const payload = (await response.json()) as { error: { code: string; message: string } };
    expect(payload.error.code).toBe("NETWORK_ERROR");
    expect(payload.error.message).toContain("网络连接失败");
  });

  it("非表单请求直接拒绝，不打扰后端", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      new Request("http://localhost/api/v1/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }),
    );

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
