/**
 * 「最近任务」测试：首页要能找回上次的任务，否则用户关掉页面后
 * 只能靠手抄地址回来（这正是「以为链接失效」的常见来源）。
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RecentTaskLink } from "@/components/RecentTaskLink";
import { readRecentTask, rememberTask } from "@/lib/recent-task";

/**
 * 测试用的内存版 localStorage。
 * 为什么不用 jsdom / Node 自带的那份：当前环境里它的实现不完整（没有 clear），
 * 而我们要验证的是「我们自己的读写逻辑」，用一个确定的替身更可靠。
 */
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
});

afterEach(() => {
  cleanup();
});

describe("最近任务", () => {
  it("没有记录时不显示任何东西（不占位、不误导）", async () => {
    render(<RecentTaskLink />);
    await waitFor(() => expect(screen.queryByText("继续查看")).toBeNull());
  });

  it("记录之后显示可点的继续入口", async () => {
    rememberTask("11111111-2222-3333-4444-555555555555");

    render(<RecentTaskLink />);

    const link = await screen.findByRole("link", { name: "继续查看" });
    expect(link.getAttribute("href")).toBe("/tasks/11111111-2222-3333-4444-555555555555");
  });

  it("记录损坏时安全降级（不抛错、不显示）", () => {
    window.localStorage.setItem("transcript-extractor.last-task", "{不是合法 JSON");
    expect(readRecentTask()).toBeNull();
    window.localStorage.setItem("transcript-extractor.last-task", JSON.stringify({ at: 1 }));
    expect(readRecentTask()).toBeNull();
  });
});
