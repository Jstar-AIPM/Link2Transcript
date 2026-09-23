/**
 * 接口客户端。
 *
 * 约定（与《第四阶段技术开发文档》第三节一致）：
 * 1. 只请求同源 `/api/v1/*`，由 Next 反向代理转发到 FastAPI（免 CORS）；
 * 2. 每个响应都过 zod 校验，校验失败按「服务异常」处理，不让脏数据进页面；
 * 3. 错误统一成 `ApiError`：优先用后端的中文文案（用户可见文案一律来自后端），
 *    只有网络层失败才使用前端兜底文案；
 * 4. 单次请求 30 秒超时（阶段 3 实测：机器高负载时接口可能十几秒才返回）。
 */
import {
  apiErrorEnvelopeSchema,
  publicConfigSchema,
  segmentsResponseSchema,
  taskCreatedSchema,
  taskStatusResponseSchema,
  transcriptResultSchema,
  type PublicConfig,
  type SegmentsResponse,
  type TaskCreated,
  type TaskStatusResponse,
  type TranscriptResult,
} from "./schemas";
import type { z } from "zod";

const API_PREFIX = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

/** 用户可见的兜底文案：纯中文，不暴露技术细节。 */
const NETWORK_FALLBACK_MESSAGE = "网络连接失败，请检查服务是否已启动后重试";
const TIMEOUT_FALLBACK_MESSAGE = "请求超时，请稍后重试";
const MALFORMED_FALLBACK_MESSAGE = "服务返回的数据无法识别，请稍后重试";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

type RequestOptions = {
  method?: "GET" | "POST";
  body?: BodyInit;
  headers?: Record<string, string>;
  timeoutMs?: number;
  signal?: AbortSignal;
};

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, headers, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort);

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method,
      body,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("REQUEST_TIMEOUT", TIMEOUT_FALLBACK_MESSAGE, 0);
    }
    throw new ApiError("NETWORK_ERROR", NETWORK_FALLBACK_MESSAGE, 0);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }

  const raw: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const envelope = apiErrorEnvelopeSchema.safeParse(raw);
    if (envelope.success) {
      throw new ApiError(envelope.data.error.code, envelope.data.error.message, response.status);
    }
    throw new ApiError("UNEXPECTED_ERROR", MALFORMED_FALLBACK_MESSAGE, response.status);
  }

  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    throw new ApiError("MALFORMED_RESPONSE", MALFORMED_FALLBACK_MESSAGE, response.status);
  }
  return parsed.data;
}

export const api = {
  /** 公开配置：上传上限、轮询间隔、时长上限（用于页面提示，不参与业务判断） */
  getConfig(): Promise<PublicConfig> {
    return request("/config", publicConfigSchema);
  },

  /** 按链接创建任务（B 站） */
  createTaskFromUrl(url: string): Promise<TaskCreated> {
    return request("/tasks/from-url", taskCreatedSchema, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  },

  getTask(taskId: string): Promise<TaskStatusResponse> {
    return request(`/tasks/${taskId}`, taskStatusResponseSchema);
  },

  /** 增量拉取片段：after 是上次响应里的 next_after */
  getSegments(taskId: string, after: number, limit = 500): Promise<SegmentsResponse> {
    return request(
      `/tasks/${taskId}/segments?after=${after}&limit=${limit}`,
      segmentsResponseSchema,
    );
  },

  cancelTask(taskId: string): Promise<TaskStatusResponse> {
    return request(`/tasks/${taskId}/cancel`, taskStatusResponseSchema, { method: "POST" });
  },

  getResult(taskId: string): Promise<TranscriptResult> {
    return request(`/tasks/${taskId}/result`, transcriptResultSchema);
  },
};

export const apiInternals = { API_PREFIX, DEFAULT_TIMEOUT_MS };
