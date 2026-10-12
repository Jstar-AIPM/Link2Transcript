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
  platform: z.enum(["local", "bilibili", "douyin", "xiaohongshu"]),
  source_url: z.string().nullish(),
  extract_method: z.enum(["subtitle", "speech_to_text"]).nullish(),
  subtitle_kind: z.enum(["cc", "ai"]).nullish(),
  processing_method_label: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  // 排队位置：还没开始时，前面还有多少个任务（后端新增，旧数据没有此字段）
  queue_ahead: z.number().nullish(),
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
  /** 线上环境不支持本地上传（平台请求体上限），此时前端隐藏上传入口 */
  enable_local_upload: z.boolean(),
  /** 是否强制邀请码登录（线上为 true） */
  require_auth: z.boolean(),
  /**
   * B 站登录态：valid / invalid / unknown / not_configured。
   * invalid 表示字幕路径已降级为语音转写（更慢），界面上要给出说明。
   */
  bilibili_login: z.enum(["valid", "invalid", "unknown", "not_configured"]),
});

export const loginRequestSchema = z.object({
  code: z.string().min(1).max(128),
});

export const inviteStatusSchema = z.object({
  /** 掩码后的邀请码（顶栏展示用，例如 ABCD****GH） */
  code_masked: z.string().default(""),
  is_admin: z.boolean().default(false),
  /** 是否不限次数（管理员码恒为 true） */
  unlimited: z.boolean().default(false),
  /** 剩余可用次数；不限次数为 null */
  remaining_uses: z.number().nullable().default(null),
  expires_at: z.string(),
});
export type InviteStatus = z.infer<typeof inviteStatusSchema>;

export const sessionResponseSchema = inviteStatusSchema.extend({
  token: z.string(),
});
export type SessionResponse = z.infer<typeof sessionResponseSchema>;
export type PublicConfig = z.infer<typeof publicConfigSchema>;

/* ---------------------------------------------------------------- 邀请码管理 */

export const inviteCodeSummarySchema = z.object({
  code: z.string(),
  masked: z.string(),
  source: z.enum(["env", "generated"]),
  role: z.enum(["admin", "user"]),
  max_uses: z.number(),
  uses: z.number(),
  /** 剩余次数；不限次数为 null */
  remaining: z.number().nullable(),
  created_at: z.string().nullable(),
  expires_at: z.string().nullable(),
  note: z.string(),
  revoked_at: z.string().nullable(),
  /** 当前是否可用（未停用、未过期、未用尽） */
  active: z.boolean(),
});
export type InviteCodeSummary = z.infer<typeof inviteCodeSummarySchema>;

export const inviteCodeListSchema = z.object({
  codes: z.array(inviteCodeSummarySchema),
  defaults: z.object({ uses: z.number(), days: z.number() }),
});
export type InviteCodeList = z.infer<typeof inviteCodeListSchema>;

export const createdInviteCodeSchema = z.object({ code: inviteCodeSummarySchema });
export type CreatedInviteCode = z.infer<typeof createdInviteCodeSchema>;

export const createInviteCodeRequestSchema = z.object({
  uses: z.number().int().min(0).max(1000),
  days: z.number().int().min(0).max(3650),
  note: z.string().max(80),
});

export const transcriptResultSchema = z.object({
  schema_version: z.number(),
  task_id: z.string(),
  source_type: z.enum(["local_file", "platform_url"]),
  platform: z.enum(["local", "bilibili", "douyin", "xiaohongshu"]),
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
  error: z.object({
    code: z.string(),
    message: z.string(),
    /** 追踪号：用户报错时报这个编号，便于在日志里定位（阶段 6 新增） */
    trace_id: z.string().optional(),
  }),
});
