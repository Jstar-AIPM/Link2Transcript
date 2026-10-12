/**
 * 邀请码管理页（阶段 6.6）前端测试。
 *
 * 覆盖：非管理员无权限提示、列表状态与文案映射、生成、停用（只对生成的码）、
 * 以及管理员码那一行只能看不能停用。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminScreen } from "@/features/admin/AdminScreen";
import type { AdminInvite } from "@/features/admin/types";
import { clearAuth, saveAuth } from "@/features/auth/storage";
import type { InviteStatus } from "@/features/auth/types";

const ENV_ROW: AdminInvite = {
  code: "ADMI-CODE",
  masked: "ADMI****DE",
  source: "env",
  role: "admin",
  max_uses: 0,
  uses: 12,
  remaining: null,
  created_at: null,
  expires_at: null,
  note: "",
  revoked_at: null,
  active: true,
};

const ACTIVE_ROW: AdminInvite = {
  code: "WXYZ-2345",
  masked: "WXYZ****45",
  source: "generated",
  role: "user",
  max_uses: 20,
  uses: 0,
  remaining: 20,
  created_at: "2026-10-12T10:00:00+08:00",
  expires_at: "2026-11-11T10:00:00+08:00",
  note: "面试-张三",
  revoked_at: null,
  active: true,
};

const EXHAUSTED_ROW: AdminInvite = {
  ...ACTIVE_ROW,
  code: "PQRS-6789",
  masked: "PQRS****89",
  max_uses: 3,
  uses: 3,
  remaining: 0,
  note: "",
  expires_at: "2026-10-24T10:00:00+08:00",
  active: false,
};

const REVOKED_ROW: AdminInvite = {
  ...ACTIVE_ROW,
  code: "STOP-0000",
  masked: "STOP****00",
  uses: 1,
  remaining: 19,
  note: "",
  revoked_at: "2026-10-13T10:00:00+08:00",
  active: false,
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
    code_masked: "ADMI****DE",
    is_admin: true,
    unlimited: true,
    remaining_uses: null,
    expires_at: "2026-11-11T10:00:00+08:00",
    ...overrides,
  };
}

beforeEach(() => {
  installMemoryStorage();
  clearAuth();
  saveAuth("admin-token", statusOf());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockAdminApi(rows: AdminInvite[]) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.includes("/revoke")) {
        return jsonResponse({ code: { ...ACTIVE_ROW, revoked_at: "2026-10-13T10:00:00+08:00" } });
      }
      if (init?.method === "POST") {
        return jsonResponse({ code: { ...ACTIVE_ROW, code: "NEWC-ODE1", uses: 0 } });
      }
      return jsonResponse({ codes: rows, defaults: { uses: 20, days: 30 } });
    }),
  );
  return calls;
}

describe("邀请码管理页", () => {
  it("非管理员显示权限不足", async () => {
    clearAuth();
    saveAuth("user-token", statusOf({ is_admin: false, unlimited: false, remaining_uses: 19 }));
    mockAdminApi([ACTIVE_ROW]);

    render(<AdminScreen />);

    await screen.findByText("邀请码管理只有管理员能用");
  });

  it("列出管理员码与生成的码，状态与用量文案正确", async () => {
    mockAdminApi([ENV_ROW, ACTIVE_ROW, EXHAUSTED_ROW, REVOKED_ROW]);

    render(<AdminScreen />);

    await screen.findByText("WXYZ-2345");
    // 管理员码：来源=环境变量、不限次数（已用 12）、不过期
    expect(screen.getByText("ADMI-CODE")).toBeDefined();
    expect(screen.getByText("不限次数（已用 12）")).toBeDefined();
    expect(screen.getAllByText("环境变量").length).toBe(1);
    expect(screen.getAllByText("生成").length).toBe(3);
    expect(screen.getAllByText("不过期").length).toBe(1);
    // 状态：可用 ×2（管理员码 + WXYZ）、次数用完、已停用
    expect(screen.getAllByText("可用").length).toBe(2);
    expect(screen.getByText("次数用完")).toBeDefined();
    expect(screen.getByText("已停用")).toBeDefined();
    // 备注显示在码下方
    expect(screen.getByText("面试-张三")).toBeDefined();
  });

  it("停用按钮只出现在未停用的生成码上，环境变量行不可停用", async () => {
    mockAdminApi([ENV_ROW, ACTIVE_ROW, EXHAUSTED_ROW, REVOKED_ROW]);

    render(<AdminScreen />);
    await screen.findByText("WXYZ-2345");

    // 只有 WXYZ 与 PQRS 可以停用（STOP 已停用、ADMI 是环境变量）
    expect(screen.getAllByRole("button", { name: "停用" }).length).toBe(2);
    // 环境变量行与已停用行都是「—」
    expect(screen.getAllByText("—").length).toBe(2);
  });

  it("生成邀请码后显示刚生成的码并刷新列表", async () => {
    const calls = mockAdminApi([ENV_ROW, ACTIVE_ROW]);

    render(<AdminScreen />);
    await screen.findByText("WXYZ-2345");

    fireEvent.click(screen.getByRole("button", { name: "生成邀请码" }));

    await screen.findByText("刚生成的邀请码");
    expect(screen.getByText("NEWC-ODE1")).toBeDefined();
    await waitFor(() => expect(calls.some((call) => call.startsWith("POST"))).toBe(true));
  });
});
