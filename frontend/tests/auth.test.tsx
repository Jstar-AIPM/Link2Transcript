/**
 * 邀请码门禁（阶段 6.6）前端测试。
 *
 * 覆盖：未登录时整页门禁、登录成功后放行、错误文案来自后端、本地开发不强制登录、
 * 顶栏登录态（掩码 / 次数 / 管理入口 / 退出）、会话失效时清掉令牌并给出提示。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InviteBadge } from "@/features/auth/InviteBadge";
import { InviteGate } from "@/features/auth/InviteGate";
import { clearAuth, getAuthSnapshot, readToken, saveAuth } from "@/features/auth/storage";
import type { InviteStatus } from "@/features/auth/types";
import { ApiError, api } from "@/lib/api/client";

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as { config: Record<string, unknown> };

const SESSION = {
  token: "session-token",
  expires_at: "2026-11-11T10:00:00+08:00",
  is_admin: false,
  remaining_uses: 19,
  code_masked: "ABCD****GH",
  unlimited: false,
};

function jsonResponse(payload: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => payload } as unknown as Response;
}

function installMemoryStorage() {
  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => (store.has(key) ? (store.get(key) as string) : null),
      setItem: (key: string, value: string) => void store.set(key, String(value)),
      removeItem: (key: string) => void store.delete(key),
      clear: () => store.clear(),
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}

function statusOf(overrides: Partial<InviteStatus> = {}): InviteStatus {
  return {
    code_masked: "ABCD****GH",
    is_admin: false,
    unlimited: false,
    remaining_uses: 19,
    expires_at: "2026-11-11T10:00:00+08:00",
    ...overrides,
  };
}

beforeEach(() => {
  installMemoryStorage();
  clearAuth();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("邀请码门禁", () => {
  it("未登录时渲染登录页，且不渲染受保护内容", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ...samples.config, require_auth: true })),
    );

    render(
      <InviteGate>
        <p>受保护的内容</p>
      </InviteGate>,
    );

    await screen.findByText("输入邀请码开始使用");
    expect(screen.getByText("邀请制 · 内测中")).toBeDefined();
    expect(screen.queryByText("受保护的内容")).toBeNull();
  });

  it("输入邀请码成功后保存令牌并放行", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/config")) return jsonResponse({ ...samples.config, require_auth: true });
        if (url.includes("/auth/me")) return jsonResponse(SESSION);
        return jsonResponse(SESSION);
      }),
    );

    render(
      <InviteGate>
        <p>受保护的内容</p>
      </InviteGate>,
    );

    const input = await screen.findByLabelText("邀请码");
    fireEvent.change(input, { target: { value: "abcd-efgh" } });
    fireEvent.click(screen.getByRole("button", { name: "进入" }));

    await screen.findByText("受保护的内容");
    expect(readToken()).toBe("session-token");
  });

  it("邀请码不正确时展示后端文案，不放行", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/config")) return jsonResponse({ ...samples.config, require_auth: true });
        return jsonResponse(
          { error: { code: "INVITE_CODE_INVALID", message: "邀请码不正确，请检查后重试" } },
          false,
          401,
        );
      }),
    );

    render(
      <InviteGate>
        <p>受保护的内容</p>
      </InviteGate>,
    );

    const input = await screen.findByLabelText("邀请码");
    fireEvent.change(input, { target: { value: "WRONG-CODE" } });
    fireEvent.click(screen.getByRole("button", { name: "进入" }));

    await screen.findByText("邀请码不正确，请检查后重试");
    expect(screen.queryByText("受保护的内容")).toBeNull();
  });

  it("本地开发（require_auth=false）不强制登录", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ...samples.config, require_auth: false })),
    );

    render(
      <InviteGate>
        <p>受保护的内容</p>
      </InviteGate>,
    );

    await screen.findByText("受保护的内容");
    expect(screen.queryByText("输入邀请码开始使用")).toBeNull();
  });
});

describe("顶栏登录态", () => {
  it("普通码显示剩余次数，且没有管理入口", () => {
    saveAuth("token-1", statusOf({ remaining_uses: 19 }));
    render(<InviteBadge />);

    expect(screen.getByText(/剩余 19 次/)).toBeDefined();
    expect(screen.queryByText("邀请码管理")).toBeNull();
  });

  it("管理员显示不限次数与「邀请码管理」入口", () => {
    saveAuth("token-2", statusOf({ is_admin: true, unlimited: true, remaining_uses: null }));
    render(<InviteBadge />);

    expect(screen.getByText(/不限次数/)).toBeDefined();
    expect(screen.getByText("邀请码管理")).toBeDefined();
  });

  it("点「退出」清空登录态", () => {
    saveAuth("token-3", statusOf());
    render(<InviteBadge />);

    fireEvent.click(screen.getByRole("button", { name: "退出" }));
    expect(readToken()).toBe("");
  });
});

describe("会话失效", () => {
  it("接口返回 401 时清掉本地令牌并给出提示", async () => {
    saveAuth("expired-token", statusOf());

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: { code: "AUTH_REQUIRED", message: "请先输入邀请码进入" } }, false, 401),
      ),
    );

    await expect(api.getTask("t1")).rejects.toBeInstanceOf(ApiError);
    expect(readToken()).toBe("");
    await waitFor(() =>
      expect(getAuthSnapshot().notice).toContain("登录状态已失效"),
    );
  });
});
