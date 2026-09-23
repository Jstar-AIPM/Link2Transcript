/**
 * 契约测试：用**真实后端响应**（frontend/tests/fixtures/backend-samples.json，
 * 由阶段 3 收尾时从运行中的服务抓取）校验 zod schema。
 *
 * 价值：后端字段一旦改名、改类型或漏字段，这里立刻失败 —— 而不是等页面上
 * 出现「已生成 undefined 段」才发现。修 fixture 的唯一正确方式是重新抓一次真实响应。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  publicConfigSchema,
  segmentsResponseSchema,
  taskStatusResponseSchema,
  transcriptResultSchema,
} from "@/lib/api/schemas";

const samples = JSON.parse(
  readFileSync(resolve(process.cwd(), "tests/fixtures/backend-samples.json"), "utf-8"),
) as Record<string, unknown>;

describe("后端响应契约", () => {
  it("公开配置", () => {
    const parsed = publicConfigSchema.safeParse(samples.config);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
  });

  it("任务状态（含阶段 3 新增的进度字段）", () => {
    const parsed = taskStatusResponseSchema.safeParse(samples.status);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
    if (!parsed.success) return;
    // 这些字段是阶段 3 为界面定下的，缺一个页面就退化成「黑盒等待」
    expect(parsed.data).toHaveProperty("progress_percent");
    expect(parsed.data).toHaveProperty("segment_count");
    expect(parsed.data).toHaveProperty("stage_message");
    expect(parsed.data).toHaveProperty("cancellable");
    expect(parsed.data).toHaveProperty("partial_result_available");
  });

  it("增量片段", () => {
    const parsed = segmentsResponseSchema.safeParse(samples.segments);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
  });

  it("结构化逐字稿", () => {
    const parsed = transcriptResultSchema.safeParse(samples.result);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
  });

  it("字段改名必须让校验失败（防止 schema 过宽）", () => {
    const broken = { ...(samples.status as Record<string, unknown>) };
    broken.stage_messages = broken.stage_message; // 故意写错名字
    delete broken.stage_message;
    expect(taskStatusResponseSchema.safeParse(broken).success).toBe(false);
  });
});
