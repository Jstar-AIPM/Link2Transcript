/**
 * 后端接口的运行时校验（zod）。
 *
 * 为什么需要：TypeScript 只能约束我们自己写的代码，接口返回是不可信数据。
 * 后端字段一旦改名/改类型，这里会立刻失败，而不是让页面悄悄渲染出错误信息。
 * 样本来源：阶段 3 真实抓取的响应（见 tests/schemas.test.ts）。
 */
import { z } from "zod";

export const taskStatusSchema = z.enum([
  "pending",
  "validating",
  "checking_subtitle",
  "downloading_audio",
  "extracting_audio",
  "transcribing",
  "exporting",
  "succeeded",
  "failed",
  "cancelled",
]);
export type TaskStatus = z.infer<typeof taskStatusSchema>;

export const terminalStatuses: readonly TaskStatus[] = ["succeeded", "failed", "cancelled"];

export function isTerminal(status: TaskStatus): boolean {
  return terminalStatuses.includes(status);
}

export const taskErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  internal_type: z.string().nullish(),
  failed_stage: taskStatusSchema.nullish(),
});
export type TaskError = z.infer<typeof taskErrorSchema>;

export const taskArtifactsSchema = z.object({
  markdown: z.string().nullish(),
  txt: z.string().nullish(),
});
export type TaskArtifacts = z.infer<typeof taskArtifactsSchema>;

export const taskStatusResponseSchema = z.object({
  task_id: z.string(),
  status: taskStatusSchema,
  stage_message: z.string(),
  source_type: z.enum(["local_file", "platform_url"]),
  platform: z.enum(["local", "bilibili"]),
  source_url: z.string().nullish(),
  extract_method: z.enum(["subtitle", "speech_to_text"]).nullish(),
  subtitle_kind: z.enum(["cc", "ai"]).nullish(),
  processing_method_label: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  elapsed_seconds: z.number(),
  media_duration_seconds: z.number().nullish(),
  estimated_remaining_seconds: z.number().nullish(),
  segment_count: z.number(),
  transcribed_seconds: z.number().nullish(),
  progress_percent: z.number(),
  partial_result_available: z.boolean(),
  cancellable: z.boolean(),
  error: taskErrorSchema.nullish(),
  artifacts: taskArtifactsSchema,
});
export type TaskStatusResponse = z.infer<typeof taskStatusResponseSchema>;

export const segmentSchema = z.object({
  index: z.number(),
  start: z.number(),
  end: z.number(),
  text: z.string(),
});
export type Segment = z.infer<typeof segmentSchema>;

export const segmentsResponseSchema = z.object({
  task_id: z.string(),
  status: taskStatusSchema,
  /** true = 尚未完成的部分结果（用于页面标注「未完成」） */
  partial: z.boolean(),
  total: z.number(),
  next_after: z.number(),
  has_more: z.boolean(),
  segments: z.array(segmentSchema),
});
export type SegmentsResponse = z.infer<typeof segmentsResponseSchema>;

export const taskCreatedSchema = z.object({
  task_id: z.string(),
  status: taskStatusSchema,
});
export type TaskCreated = z.infer<typeof taskCreatedSchema>;

export const publicConfigSchema = z.object({
  max_upload_mb: z.number(),
  task_poll_interval_seconds: z.number(),
  max_media_minutes: z.number(),
});
export type PublicConfig = z.infer<typeof publicConfigSchema>;

export const transcriptResultSchema = z.object({
  schema_version: z.number(),
  task_id: z.string(),
  source_type: z.enum(["local_file", "platform_url"]),
  platform: z.enum(["local", "bilibili"]),
  source_url: z.string().nullish(),
  original_filename: z.string(),
  media_type: z.enum(["audio", "video"]),
  processing_method: z.string(),
  subtitle_kind: z.enum(["cc", "ai"]).nullish(),
  language: z.string().nullish(),
  duration_seconds: z.number().nullish(),
  text: z.string(),
  segments: z.array(z.object({ start: z.number(), end: z.number(), text: z.string() })),
  generated_at: z.string(),
});
export type TranscriptResult = z.infer<typeof transcriptResultSchema>;

/** 后端统一错误结构：{ error: { code, message } } */
export const apiErrorEnvelopeSchema = z.object({
  error: z.object({ code: z.string(), message: z.string() }),
});
