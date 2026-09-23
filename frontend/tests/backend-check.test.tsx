/**
 * 组件测试：服务连接自检卡。
 *
 * 覆盖三件在真实使用中会出问题的事：
 * 1. 连上后端后，卡片必须从「检查中」变成「已连接」并显示真实配置；
 * 2. 后端没启动时，必须给中文原因 + 可点的「重新检查」；
 * 3. 恢复后重试要能成功（不是点一次就永久失败）。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendCheck } from "@/components/BackendCheck";

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as { config: unknown };

function jsonResponse(payload: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => payload,
  } as unknown as Response;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("服务连接自检卡", () => {
  beforeEach(() => {
    expect(samples.config).toBeDefined();
  });

  it("连上后端后显示已连接与真实配置", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(samples.config));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<BackendCheck />);
    expect(container.textContent).toContain("检查中");

    await screen.findByText("已连接");
    await waitFor(() => {
      expect(container.textContent).toContain("2048 MB");
      expect(container.textContent).toContain("2 秒");
      expect(container.textContent).toContain("360 分钟");
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/config", expect.anything());
  });

  it("后端未启动时给出中文原因与重试入口", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const { container } = render(<BackendCheck />);

    await screen.findByText("未连接");
    expect(container.textContent).toContain("网络连接失败");
    expect(screen.getByRole("button", { name: "重新检查" })).toBeDefined();
  });

  it("恢复后点重新检查能连上", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(jsonResponse(samples.config));
    vi.stubGlobal("fetch", fetchMock);

    render(<BackendCheck />);
    await screen.findByText("未连接");

    screen.getByRole("button", { name: "重新检查" }).click();
    await screen.findByText("已连接");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("后端返回结构不对时不静默通过（运行时校验生效）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ max_upload_mb: "很多" })));

    const { container } = render(<BackendCheck />);

    await screen.findByText("未连接");
    expect(container.textContent).toContain("无法识别");
  });
});
