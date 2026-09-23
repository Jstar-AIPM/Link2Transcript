/**
 * 文件上传：用 XMLHttpRequest 而不是 fetch。
 *
 * 原因只有一个，但很关键：`fetch` 拿不到**上传进度**。上传上限是 2048 MB，
 * 没有进度用户会以为卡死。XHR 的 `upload.onprogress` 能给出真实百分比，
 * 并且可以 `abort()` 取消上传（用户选错文件时不用干等）。
 *
 * 返回一个「句柄」而不是单纯的 Promise，就是为了能在界面上放「取消上传」按钮。
 */
import { ApiError } from "./client";
import { apiErrorEnvelopeSchema, taskCreatedSchema, type TaskCreated } from "./schemas";

const API_PREFIX = "/api/v1";
/** 大文件慢速网络下的最长等待时间（10 分钟） */
const UPLOAD_TIMEOUT_MS = 10 * 60 * 1000;

export type UploadHandle = {
  promise: Promise<TaskCreated>;
  abort: () => void;
};

export type UploadOptions = {
  /** 0–100；只在能算出总长度时触发 */
  onProgress?: (percent: number) => void;
  /** 100% 之后服务端仍在落盘/建任务，用它切换文案，避免「100% 却停住」的错觉 */
  onProcessing?: () => void;
};

export function uploadFile(file: File, options: UploadOptions = {}): UploadHandle {
  const xhr = new XMLHttpRequest();

  const promise = new Promise<TaskCreated>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    xhr.open("POST", `${API_PREFIX}/tasks`);
    xhr.timeout = UPLOAD_TIMEOUT_MS;

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !options.onProgress) return;
      const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
      options.onProgress(percent);
      if (percent === 100) options.onProcessing?.();
    };

    xhr.onload = () => {
      let raw: unknown = null;
      try {
        raw = JSON.parse(xhr.responseText) as unknown;
      } catch {
        reject(new ApiError("MALFORMED_RESPONSE", "服务返回的数据无法识别，请稍后重试", xhr.status));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        const parsed = taskCreatedSchema.safeParse(raw);
        if (parsed.success) {
          resolve(parsed.data);
        } else {
          reject(
            new ApiError("MALFORMED_RESPONSE", "服务返回的数据无法识别，请稍后重试", xhr.status),
          );
        }
        return;
      }
      const envelope = apiErrorEnvelopeSchema.safeParse(raw);
      if (envelope.success) {
        reject(new ApiError(envelope.data.error.code, envelope.data.error.message, xhr.status));
      } else {
        reject(new ApiError("UNEXPECTED_ERROR", "上传失败，请稍后重试", xhr.status));
      }
    };

    // 网络层失败（后端没启动、连接被切断）与超时分开说明，便于用户自救
    xhr.onerror = () =>
      reject(new ApiError("NETWORK_ERROR", "网络连接失败，请检查服务是否已启动后重试", 0));
    xhr.ontimeout = () => reject(new ApiError("REQUEST_TIMEOUT", "上传超时，请重试", 0));
    xhr.onabort = () => reject(new ApiError("UPLOAD_CANCELLED", "已取消上传", 0));

    xhr.send(form);
  });

  return { promise, abort: () => xhr.abort() };
}
