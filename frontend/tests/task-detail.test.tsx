/**
 * 任务详情页测试（4C）。
 *
 * 覆盖状态驱动的七种表现、逐段追加、取消任务的二次确认、
 * 未完成/已取消标注与失败后的下一步动作，以及自动跟随可暂停。
 * 断言的是「用户看到什么」与「有没有真的调用后端」。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TaskDetail } from "@/features/task-detail/TaskDetail";

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as {
  config: { task_poll_interval_seconds: number; max_upload_mb: number; max_media_minutes: number };
  status: Record<string, unknown>;
  segments: { segments: unknown[]; next_after: number; total: number };
};

const TASK_ID = "11111111-2222-3333-4444-555555555555";

function jsonResponse(payload: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => payload } as unknown as Response;
}

/** 用真实 fixture 的字段，按需覆盖状态字段 */
function taskWith(overrides: Record<string, unknown>) {
  return {
    ...samples.status,
    task_id: TASK_ID,
    ...overrides,
  };
}

function segmentsPage(items: { index: number; start: number; end: number; text: string }[], nextAfter: number) {
  return { ...samples.segments, task_id: TASK_ID, segments: items, next_after: nextAfter };
}

const CONFIG = { ...samples.config, task_poll_interval_seconds: 0.2 };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

type Route =
  | { kind: "status"; value: Record<string, unknown> }
  | { kind: "segments"; value: unknown };

/** 用一串「第 n 次轮询返回什么」来驱动页面 */
function stubFetch(script: { status?: Route["value"]; segments?: unknown }[]) {
  let step = 0; // 第几次轮询（状态请求先到，片段请求随之）
  const entry = (index: number) => script[Math.min(Math.max(0, index), script.length - 1)];

  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const target = String(url);
    if (target.includes("/api/v1/config")) return Promise.resolve(jsonResponse(CONFIG));
    if (target.includes("/cancel")) {
      expect(init?.method).toBe("POST"); // 取消必须是 POST，不允许用 GET 触发副作用
      return Promise.resolve(
        jsonResponse(
          taskWith({
            status: "cancelled",
            stage_message: "任务已取消",
            cancellable: false,
          }),
        ),
      );
    }
    if (target.includes("/segments")) {
      const page = (entry(step - 1)?.segments ?? segmentsPage([], 0)) as {
        segments: { index: number }[];
        next_after: number;
        total: number;
      };
      const after = Number(new URL(target, "http://localhost").searchParams.get("after") ?? 0);
      const fresh = page.segments.filter((segment) => segment.index >= after);
      return Promise.resolve(jsonResponse({ ...page, segments: fresh, next_after: fresh.length ? Math.max(...fresh.map((s) => s.index)) + 1 : after }));
    }
    if (target.includes("/tasks/")) {
      const hit = entry(step);
      step += 1;
      return Promise.resolve(jsonResponse(hit?.status ?? taskWith({})));
    }
    return Promise.resolve(jsonResponse({}, false, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("任务详情：状态与进度", () => {
  it("转写中显示阶段文案、进度、已转写/共与已生成段数", async () => {
    stubFetch([
      {
        status: taskWith({
          status: "transcribing",
          stage_message: "正在生成逐字稿（已生成 2 段）",
          segment_count: 2,
          transcribed_seconds: 42,
          media_duration_seconds: 600,
          progress_percent: 7,
          cancellable: true,
        }),
        segments: segmentsPage(
          [
            { index: 0, start: 0, end: 20, text: "第一段内容" },
            { index: 1, start: 20, end: 42, text: "第二段内容" },
          ],
          2,
        ),
      },
    ]);

    const { container } = render(<TaskDetail taskId={TASK_ID} />);

    await screen.findByText("正在生成逐字稿（已生成 2 段）");
    expect(container.textContent).toContain("已转写 0分42秒 / 共 10分00秒");
    expect(container.textContent).toContain("已生成 2 段");
    expect(container.textContent).toContain("进度 7%");
    expect(container.textContent).toContain("进行中");
    expect(screen.getByText("第一段内容")).toBeDefined();
    expect(screen.getByText("第二段内容")).toBeDefined();
    expect(screen.getByRole("button", { name: "取消任务" })).toBeDefined();
  });

  it("片段随轮询逐段追加，且顺序与序号一致", async () => {
    stubFetch([
      {
        status: taskWith({ status: "transcribing", cancellable: true, segment_count: 1 }),
        segments: segmentsPage([{ index: 0, start: 0, end: 5, text: "先来的" }], 1),
      },
      {
        status: taskWith({ status: "transcribing", cancellable: true, segment_count: 2 }),
        segments: segmentsPage([{ index: 1, start: 5, end: 9, text: "后来的" }], 2),
      },
    ]);

    const { container } = render(<TaskDetail taskId={TASK_ID} />);

    await screen.findByText("先来的");
    await screen.findByText("后来的");
    const texts = Array.from(container.querySelectorAll("li")).map((li) => li.textContent ?? "");
    expect(texts[0]).toContain("先来的");
    expect(texts[1]).toContain("后来的");
  });

  it("还没有片段时给出可解释的空状态，而不是空白", async () => {
    stubFetch([
      {
        status: taskWith({
          status: "transcribing",
          stage_message: "正在加载语音识别模型",
          segment_count: 0,
          cancellable: true,
        }),
        segments: segmentsPage([], 0),
      },
    ]);

    render(<TaskDetail taskId={TASK_ID} />);

    await screen.findByText("正在加载语音识别模型");
    await screen.findByText(/正在等待第一段内容/);
  });

  it("成功后给出下载入口与总耗时", async () => {
    stubFetch([
      {
        status: taskWith({
          status: "succeeded",
          stage_message: "逐字稿已生成",
          progress_percent: 100,
          segment_count: 3,
          elapsed_seconds: 125,
          cancellable: false,
          artifacts: {
            markdown: "/api/v1/tasks/x/download/markdown",
            txt: "/api/v1/tasks/x/download/txt",
          },
        }),
        segments: segmentsPage([{ index: 0, start: 0, end: 5, text: "完成了" }], 1),
      },
    ]);

    const { container } = render(<TaskDetail taskId={TASK_ID} />);

    await screen.findByText("逐字稿已生成");
    expect(container.textContent).toContain("已完成");
    expect(container.textContent).toContain("总耗时");
    // 阶段 6B：下载改为带鉴权的按钮（令牌放请求头，不进 URL）
    expect(screen.getByRole("button", { name: "下载 Markdown" })).toBeDefined();
    expect(screen.getByRole("button", { name: "下载 TXT" })).toBeDefined();
    // 终态不再显示取消入口
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
  });
});

describe("任务详情：失败与取消", () => {
  it("失败时标注未完成、说明原因、保留内容并给出下一步", async () => {
    stubFetch([
      {
        status: taskWith({
          status: "failed",
          stage_message: "未在该视频中检测到人声。请确认视频是否包含人声内容。",
          error: { code: "SILENT_AUDIO", message: "未在该视频中检测到人声。请确认视频是否包含人声内容。", failed_stage: "transcribing" },
          partial_result_available: true,
          cancellable: false,
          artifact: undefined,
        }),
        segments: segmentsPage([{ index: 0, start: 0, end: 5, text: "失败前的内容" }], 1),
      },
    ]);

    const { container } = render(<TaskDetail taskId={TASK_ID} />);

    await screen.findByText(/未在该视频中检测到人声/);
    expect(container.textContent).toContain("未完成");
    expect(container.textContent).toContain("已生成的内容保留在下方");
    expect(screen.getByText("失败前的内容")).toBeDefined();
    expect(screen.getByRole("link", { name: "重新发起任务" })).toBeDefined();
    // 失败不提供下载
    expect(screen.queryByRole("button", { name: "下载 Markdown" })).toBeNull();
  });

  it("取消任务需要二次确认，确认后调用后端并显示已取消", async () => {
    const fetchMock = stubFetch([
      {
        status: taskWith({
          status: "transcribing",
          stage_message: "正在生成逐字稿（已生成 1 段）",
          cancellable: true,
          segment_count: 1,
        }),
        segments: segmentsPage([{ index: 0, start: 0, end: 5, text: "取消前的内容" }], 1),
      },
    ]);

    const { container } = render(<TaskDetail taskId={TASK_ID} />);
    await screen.findByText("取消前的内容");

    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    expect(screen.getByText(/确定取消吗？已生成的内容会保留/)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "继续等待" }));
    expect(screen.queryByText(/确定取消吗？/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    fireEvent.click(screen.getByRole("button", { name: "确认取消" }));

    await screen.findByText("任务已取消");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/cancel") && (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(container.textContent).toContain("已取消");
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    // 取消后内容仍保留
    expect(screen.getByText("取消前的内容")).toBeDefined();
  });
});

describe("任务详情：任务不存在、复制全文与最近任务", () => {
  it("任务不存在时给明确说明与返回入口，而不是一直重试", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (String(url).includes("/api/v1/config")) return Promise.resolve(jsonResponse(CONFIG));
        return Promise.resolve(
          jsonResponse({ error: { code: "TASK_NOT_FOUND", message: "未找到该任务" } }, false, 404),
        );
      }),
    );

    render(<TaskDetail taskId="2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11" />);

    await screen.findByText("没有找到这个任务");
    expect(screen.getByRole("link", { name: "返回首页，发起新任务" })).toBeDefined();
    expect(document.body.textContent).toContain("2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11");
  });

  it("有内容时可以复制全文，剪贴板不可用时给中文提示", async () => {
    stubFetch([
      {
        status: taskWith({ status: "transcribing", cancellable: true, segment_count: 2 }),
        segments: segmentsPage(
          [
            { index: 0, start: 0, end: 5, text: "第一段" },
            { index: 1, start: 5, end: 9, text: "第二段" },
          ],
          2,
        ),
      },
    ]);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    render(<TaskDetail taskId={TASK_ID} />);
    await screen.findByText("第二段");

    fireEvent.click(screen.getByRole("button", { name: "复制全文" }));
    await screen.findByText("已复制");
    expect(writeText).toHaveBeenCalledWith("第一段\n第二段");
  });
});

describe("任务详情：自动跟随", () => {
  it("用户上滚时暂停跟随并出现「回到最新」，点击后恢复", async () => {
    stubFetch([
      {
        status: taskWith({ status: "transcribing", cancellable: true, segment_count: 3 }),
        segments: segmentsPage(
          [
            { index: 0, start: 0, end: 5, text: "第一段" },
            { index: 1, start: 5, end: 9, text: "第二段" },
            { index: 2, start: 9, end: 12, text: "第三段" },
          ],
          3,
        ),
      },
    ]);

    render(<TaskDetail taskId={TASK_ID} />);
    await screen.findByText("第三段");

    const scroller = screen.getByTestId("transcript-scroll");
    // jsdom 不做布局：手动给出尺寸，模拟「已经滚到中间、离底部很远」
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 100, configurable: true });
    scroller.scrollTop = 100;

    fireEvent.scroll(scroller);
    const button = await screen.findByRole("button", { name: "回到最新" });

    // 滚回底部后按钮消失（恢复自动跟随）
    scroller.scrollTop = 900;
    fireEvent.scroll(scroller);
    await waitFor(() => expect(screen.queryByRole("button", { name: "回到最新" })).toBeNull());
    expect(button).toBeDefined();
  });
});
