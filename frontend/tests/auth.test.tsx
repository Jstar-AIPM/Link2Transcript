/**
 * 邀请码登录（阶段 6B）前端测试。
 *
 * 覆盖：登录成功后保存令牌并跳转、错误文案来自后端、会话失效时清掉令牌、
 * 以及"需要登录但没登录"时提前跳登录页（不让用户先填一堆再被弹回去）。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeGate } from "@/features/auth/HomeGate";
import { LoginPanel } from "@/features/auth/LoginPanel";
import { ApiError, api } from "@/lib/api/client";
import { clearToken, readToken } from "@/lib/session";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace, prefetch: vi.fn() }),
}));

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as { config: Record<string, unknown> };

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

beforeEach(() => {
  installMemoryStorage();
  replace.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("登录面板", () => {
  it("登录成功后保存令牌并回到首页", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          token: "session-token",
          expires_at: "2026-04-01T10:00:00+08:00",
          is_admin: false,
          remaining_uses: 19,
        }),
      ),
    );

    render(<LoginPanel />);
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "abcd-efgh" } });
    fireEvent.click(screen.getByRole("button", { name: "进入" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(readToken()).toBe("session-token");
  });

  it("邀请码不正确时展示后端文案，不跳转", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "INVITE_CODE_INVALID",
              message: "邀请码不正确，请确认后重试",
              trace_id: "abc12345",
            },
          },
          false,
          401,
        ),
      ),
    );

    render(<LoginPanel />);
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "WRONG-CODE" } });
    fireEvent.click(screen.getByRole("button", { name: "进入" }));

    await screen.findByText("邀请码不正确，请确认后重试");
    // 追踪号展示出来，便于用户报错时报编号
    expect(screen.getByText(/问题编号：abc12345/)).toBeDefined();
    expect(replace).not.toHaveBeenCalled();
  });

  it("空邀请码不发请求", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<LoginPanel />);
    fireEvent.click(screen.getByRole("button", { name: "进入" }));

    await screen.findByText("请输入邀请码");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("会话失效", () => {
  it("接口返回 401 时清掉本地令牌（让界面回到登录页）", async () => {
    installMemoryStorage();
    const { saveToken } = await import("@/lib/session");
    saveToken("expired-token", false);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: "AUTH_REQUIRED", message: "请先输入邀请码进入" } }, false, 401),
      ),
    );

    await expect(api.getTask("t1")).rejects.toBeInstanceOf(ApiError);
    expect(readToken()).toBe("");
  });
});

describe("登录守卫", () => {
  it("配置要求登录且本地无令牌时，直接跳登录页且不渲染内容", async () => {
    clearToken();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ...samples.config, require_auth: true })),
    );

    render(
      <HomeGate>
        <p>受保护的内容</p>
      </HomeGate>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("受保护的内容")).toBeNull();
  });

  it("本地开发（不要求登录）时正常渲染内容", async () => {
    clearToken();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ...samples.config, require_auth: false })),
    );

    render(
      <HomeGate>
        <p>受保护的内容</p>
      </HomeGate>,
    );

    await screen.findByText("受保护的内容");
    expect(replace).not.toHaveBeenCalled();
  });
});
