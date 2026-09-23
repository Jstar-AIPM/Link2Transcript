"use client";

/**
 * 任务详情（4B 最小可用版）。
 *
 * 现在保证的是：能看见阶段、能看见进度、能看到内容一段段出现、能拿到结果；
 * 4C 会补上进度条细节、自动跟随可暂停、取消任务、未完成标注与失败后的下一步。
 */
import Link from "next/link";

import { ProgressBar } from "@/components/ProgressBar";
import { useServiceConfig } from "@/features/service-config/useServiceConfig";
import { useTaskPolling } from "@/features/task-detail/useTaskPolling";
import { formatDuration, formatPercent, formatTimestamp } from "@/lib/format";

const FALLBACK_INTERVAL_SECONDS = 2;

export function TaskDetail({ taskId }: { taskId: string }) {
  const { state: configState } = useServiceConfig();
  const interval =
    configState.kind === "ready"
      ? configState.config.task_poll_interval_seconds
      : FALLBACK_INTERVAL_SECONDS;

  const { status, segments, notice, error, loading, refresh } = useTaskPolling(taskId, interval);

  const isRunning = status !== null && !["succeeded", "failed", "cancelled"].includes(status.status);

  return (
    <section className="rounded-card border border-hairline bg-raised p-4 shadow-subtle sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[20px] font-medium text-ink">
          {status ? status.stage_message : loading ? "正在读取任务" : "任务状态未知"}
        </h2>
        {status ? (
          <span className="tnum rounded-chip bg-neutral-status-soft px-2.5 py-1 text-[12px] text-neutral-status">
            {status.status === "succeeded"
              ? "已完成"
              : status.status === "failed"
                ? "未完成"
                : status.status === "cancelled"
                  ? "已取消"
                  : "进行中"}
          </span>
        ) : null}
      </div>

      {status ? (
        <>
          <div className="mt-3">
            <ProgressBar percent={status.progress_percent} label="转写进度" />
            <p className="tnum mt-2 text-[13px] text-ink-soft">
              {status.media_duration_seconds
                ? `已转写 ${formatDuration(status.transcribed_seconds)} / 共 ${formatDuration(status.media_duration_seconds)}`
                : "正在准备音频"}
              {status.segment_count > 0 ? ` · 已生成 ${status.segment_count} 段` : ""}
              {` · 进度 ${formatPercent(status.progress_percent)}`}
            </p>
          </div>

          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] text-ink-soft sm:grid-cols-2">
            <div className="flex gap-2">
              <dt>处理方式</dt>
              <dd className="text-ink">{status.processing_method_label}</dd>
            </div>
            {status.media_duration_seconds ? (
              <div className="flex gap-2">
                <dt>内容时长</dt>
                <dd className="tnum text-ink">{formatDuration(status.media_duration_seconds)}</dd>
              </div>
            ) : null}
          </dl>
        </>
      ) : null}

      {notice ? (
        <p className="mt-3 rounded-input bg-warning-soft px-3 py-2 text-[14px] text-warning">
          {notice}
        </p>
      ) : null}

      {error ? (
        <div className="mt-3 rounded-input bg-danger-soft px-3 py-2">
          <p className="text-[14px] text-danger">{error}</p>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-2 rounded-button border border-hairline-strong px-3 py-1.5 text-[14px] text-ink transition-colors hover:bg-canvas"
          >
            重新加载
          </button>
        </div>
      ) : null}

      {status?.status === "failed" && status.error ? (
        <p className="mt-3 rounded-input bg-danger-soft px-3 py-2 text-[14px] text-danger">
          {status.error.message}
        </p>
      ) : null}

      {status?.status === "cancelled" ? (
        <p className="mt-3 rounded-input bg-warning-soft px-3 py-2 text-[14px] text-warning">
          任务已取消：已生成的内容保留在下方，需要完整逐字稿请重新发起任务。
        </p>
      ) : null}

      <h3 className="mt-5 text-[15px] font-medium text-ink">逐字稿</h3>
      <div className="mt-2 max-h-[420px] overflow-auto rounded-input border border-hairline bg-canvas px-4 py-3">
        {segments.length === 0 ? (
          <p className="text-[14px] text-ink-muted">
            {isRunning ? "正在等待第一段内容…" : "没有可展示的内容。"}
          </p>
        ) : (
          <ol className="space-y-1.5">
            {segments.map((segment) => (
              <li key={segment.index} className="flex gap-3">
                <span className="timestamp mt-0.5 shrink-0 text-[13px] text-ink-muted">
                  {formatTimestamp(segment.start)}
                </span>
                <span className="text-[16px] leading-[1.75] text-ink">{segment.text}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      {status?.status === "succeeded" && status.artifacts.markdown ? (
        <div className="mt-4 flex flex-wrap gap-3">
          <a
            href={status.artifacts.markdown}
            className="rounded-button bg-ink-fill px-4 py-2.5 text-[15px] text-ink-inverse transition-opacity hover:opacity-90"
          >
            下载 Markdown
          </a>
          {status.artifacts.txt ? (
            <a
              href={status.artifacts.txt}
              className="rounded-button border border-hairline-strong px-4 py-2.5 text-[15px] text-ink transition-colors hover:bg-sunken"
            >
              下载 TXT
            </a>
          ) : null}
        </div>
      ) : null}

      <p className="mt-5 text-[13px] text-ink-muted">
        <Link href="/" className="underline underline-offset-2 hover:text-ink">
          返回首页，发起新任务
        </Link>
      </p>
    </section>
  );
}
