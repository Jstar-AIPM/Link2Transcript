/**
 * 创建任务面板测试（4B）。
 *
 * 覆盖用户真实会遇到的路径：格式不支持、太大、正常上传、取消上传、
 * 链接格式不对、链接提交成功、以及「后端没启动」时的自救入口。
 * 断言的是**用户能看到什么**（中文文案）与**有没有真的发请求**。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateTaskPanel } from "@/features/create-task/CreateTaskPanel";
import { ApiError } from "@/lib/api/client";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn() }),
}));

const uploadFileMock = vi.fn();
vi.mock("@/lib/api/upload", () => ({
  uploadFile: (...args: unknown[]) => uploadFileMock(...args),
}));

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as { config: unknown };

function jsonResponse(payload: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => payload } as unknown as Response;
}

function fileWithSize(name: string, size: number): File {
  const file = new File(["x"], name, { type: "audio/mpeg" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

const configFetch = () => vi.fn().mockResolvedValue(jsonResponse(samples.config));

beforeEach(() => {
  push.mockClear();
  uploadFileMock.mockReset();
  vi.stubGlobal("fetch", configFetch());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function renderPanel() {
  const view = render(<CreateTaskPanel />);
  await waitFor(() => expect(document.body.textContent).toContain("单个文件不超过 2048 MB"));
  return view;
}

describe("本地文件模式", () => {
  it("格式不支持时立刻提示，不发起上传", async () => {
    uploadFileMock.mockReturnValue({ promise: Promise.resolve({}), abort: vi.fn() });
    await renderPanel();

    const input = document.querySelector("#file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fileWithSize("note.txt", 1024)] } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText(/当前不支持该文件格式/);
    expect(uploadFileMock).not.toHaveBeenCalled();
  });

  it("超过大小上限时提示与后端一致的文案", async () => {
    await renderPanel();

    const input = document.querySelector("#file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fileWithSize("big.m4a", 3000 * 1024 * 1024)] } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText("文件不能超过 2048 MB");
    expect(uploadFileMock).not.toHaveBeenCalled();
  });

  it("正常上传：显示进度、成功后跳到任务页", async () => {
    let resolveUpload: (value: { task_id: string; status: string }) => void = () => {};
    const abort = vi.fn();
    uploadFileMock.mockImplementation((_file: File, options: { onProgress?: (p: number) => void }) => {
      options.onProgress?.(42);
      return {
        promise: new Promise((resolve) => {
          resolveUpload = resolve as typeof resolveUpload;
        }),
        abort,
      };
    });

    await renderPanel();
    const input = document.querySelector("#file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fileWithSize("podcast.m4a", 1024)] } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText("正在上传 42%");
    resolveUpload({ task_id: "task-42", status: "pending" });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/tasks/task-42"));
  });

  it("上传中可以取消，取消后给出中性提示而不是报错", async () => {
    let rejectUpload: (error: unknown) => void = () => {};
    uploadFileMock.mockReturnValue({
      promise: new Promise((_resolve, reject) => {
        rejectUpload = reject;
      }),
      abort: vi.fn(),
    });

    await renderPanel();
    const input = document.querySelector("#file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fileWithSize("podcast.m4a", 1024)] } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    fireEvent.click(await screen.findByRole("button", { name: "取消上传" }));
    rejectUpload(new ApiError("UPLOAD_CANCELLED", "已取消上传", 0));

    await screen.findByText(/已取消上传。你可以重新选择文件后再提交。/);
    expect(push).not.toHaveBeenCalled();
  });
});

describe("B 站链接模式", () => {
  async function switchToUrl() {
    fireEvent.click(screen.getByRole("tab", { name: "粘贴 B 站链接" }));
    return (await screen.findByLabelText("B 站视频链接")) as HTMLInputElement;
  }

  it("默认给出多 P 与时长上限提示", async () => {
    await renderPanel();
    await switchToUrl();
    expect(document.body.textContent).toContain("多 P 视频请粘贴某一个分集的链接");
    expect(document.body.textContent).toContain("单条内容时长上限 360 分钟");
  });

  it("链接格式不对时给出与后端一致的提示", async () => {
    await renderPanel();
    const input = await switchToUrl();
    fireEvent.change(input, { target: { value: "随便写的东西" } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await screen.findByText("链接格式不正确，请粘贴完整的 B 站视频链接");
    expect(push).not.toHaveBeenCalled();
  });

  it("合法链接提交成功后跳转", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).includes("/tasks/from-url")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(jsonResponse({ task_id: "bv-1", status: "pending" }, true, 202));
      }
      return Promise.resolve(jsonResponse(samples.config));
    });
    vi.stubGlobal("fetch", fetchMock);

    await renderPanel();
    const input = await switchToUrl();
    fireEvent.change(input, {
      target: { value: "https://www.bilibili.com/video/BV1VVhk6pEiR" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/tasks/bv-1"));
  });
});

describe("线上关闭本地上传时（只支持链接）", () => {
  it("隐藏上传入口、给出说明，且链接模式仍可提交", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).includes("/tasks/from-url")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(jsonResponse({ task_id: "bv-9", status: "pending" }, true, 202));
      }
      return Promise.resolve(
        jsonResponse({ ...(samples.config as object), enable_local_upload: false }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CreateTaskPanel />);

    await screen.findByText(/当前环境只支持粘贴 B 站视频链接/);
    // 上传入口消失：没有模式切换、也没有文件选择框
    expect(screen.queryByRole("tab", { name: "上传本地文件" })).toBeNull();
    expect(document.querySelector("#file-input")).toBeNull();

    const input = (await screen.findByLabelText("B 站视频链接")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://www.bilibili.com/video/BV1VVhk6pEiR" } });
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/tasks/bv-9"));
  });
});

describe("服务不可用", () => {
  it("读不到配置时给出重试入口并禁用提交", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<CreateTaskPanel />);

    await screen.findByText(/网络连接失败/);
    expect(screen.getByRole("button", { name: "重新检查" })).toBeDefined();
    expect(screen.getByRole("button", { name: "开始提取" })).toHaveProperty("disabled", true);
  });
});
