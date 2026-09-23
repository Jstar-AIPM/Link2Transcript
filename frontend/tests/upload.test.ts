/**
 * 上传层测试（4B）。
 *
 * 上传用 XHR 而不是 fetch，就是为了拿到**真实进度**与**可取消**。
 * 这里用一个假的 XMLHttpRequest 精确驱动各种情形，验证：
 * 进度百分比、100% 后的「服务端处理中」、后端错误文案透传、
 * 网络失败与超时的中文兜底、以及取消上传。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { uploadFile } from "@/lib/api/upload";

type ProgressHandler = (event: {
  lengthComputable: boolean;
  loaded: number;
  total: number;
}) => void;

class FakeXHR {
  static last: FakeXHR;
  /** progress 属于 upload（上传字节），load/error/timeout/abort 属于 XHR 本身 */
  upload: { onprogress?: ProgressHandler } = {};
  onload?: () => void;
  onerror?: () => void;
  ontimeout?: () => void;
  onabort?: () => void;
  status = 0;
  responseText = "";
  timeout = 0;
  aborted = false;
  sentBody: FormData | null = null;

  constructor() {
    FakeXHR.last = this;
  }

  open(): void {
    /* 测试替身：不需要真的建立连接 */
  }
  send(body: FormData) {
    this.sentBody = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  // ---- 测试驱动用 ----
  emitProgress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total });
  }
  emitLoad(status: number, body: unknown) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.onload?.();
  }
}

function makeFile(): File {
  return new File(["hello"], "podcast.m4a", { type: "audio/x-m4a" });
}

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("文件上传", () => {
  it("上报真实上传进度，并在 100% 后提示服务端处理中", async () => {
    const percents: number[] = [];
    let processing = false;
    const handle = uploadFile(makeFile(), {
      onProgress: (percent) => percents.push(percent),
      onProcessing: () => {
        processing = true;
      },
    });

    FakeXHR.last.emitProgress(1, 4);
    FakeXHR.last.emitProgress(2, 4);
    FakeXHR.last.emitProgress(4, 4);
    FakeXHR.last.emitLoad(202, { task_id: "t-1", status: "pending" });

    await expect(handle.promise).resolves.toEqual({ task_id: "t-1", status: "pending" });
    expect(percents).toEqual([25, 50, 100]);
    expect(processing).toBe(true);
  });

  it("把后端的中文错误文案原样透传（不自己造一套说法）", async () => {
    const handle = uploadFile(makeFile());
    FakeXHR.last.emitLoad(413, {
      error: { code: "FILE_TOO_LARGE", message: "文件不能超过 2048 MB" },
    });

    await expect(handle.promise).rejects.toMatchObject({
      code: "FILE_TOO_LARGE",
      message: "文件不能超过 2048 MB",
    });
  });

  it("网络失败与超时分别给中文说明", async () => {
    const network = uploadFile(makeFile());
    FakeXHR.last.onerror?.();
    await expect(network.promise).rejects.toBeInstanceOf(ApiError);
    await expect(network.promise).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      message: "网络连接失败，请检查服务是否已启动后重试",
    });

    const timeout = uploadFile(makeFile());
    FakeXHR.last.ontimeout?.();
    await expect(timeout.promise).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
      message: "上传超时，请重试",
    });
  });

  it("返回结构不对时按服务异常处理，不静默通过", async () => {
    const handle = uploadFile(makeFile());
    FakeXHR.last.emitLoad(202, { unexpected: true });
    await expect(handle.promise).rejects.toMatchObject({ code: "MALFORMED_RESPONSE" });
  });

  it("可以取消上传", async () => {
    const handle = uploadFile(makeFile());
    handle.abort();
    expect(FakeXHR.last.aborted).toBe(true);
    await expect(handle.promise).rejects.toMatchObject({ code: "UPLOAD_CANCELLED" });
  });

  it("请求体是 multipart，字段名与后端约定一致", () => {
    uploadFile(makeFile());
    const body = FakeXHR.last.sentBody;
    expect(body).toBeInstanceOf(FormData);
    expect(body?.get("file")).toBeInstanceOf(File);
  });
});
