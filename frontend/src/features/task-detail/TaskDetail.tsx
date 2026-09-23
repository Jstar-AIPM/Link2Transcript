"use client";

/**
 * 任务详情（4C 完整版）。
 *
 * 页面由后端状态驱动，七种状态各有明确表现；三件关键事在这里落地：
 * 1. 过程可见：阶段文案 + 进度条 + 逐段追加 + 自动跟随可暂停；
 * 2. 可控：取消任务（二次确认，取消后内容保留、无下载入口）；
 * 3. 不丢：刷新后从磁盘重放（游标归零重新拉），内容与刷新前逐字一致。
 */
import Link from "next/link";
import { useState } from "react";

import { ProgressBar } from "@/components/ProgressBar";
import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptViewer } from "@/components/TranscriptViewer";
import { useServiceConfig } from "@/features/service-config/useServiceConfig";
import { useTaskPolling } from "@/features/task-detail/useTaskPolling";
import { formatDuration, formatPercent } from "@/lib/format";
import { isTerminal } from "@/lib/api/schemas";

const FALLBACK_INTERVAL_SECONDS = 2;

export function TaskDetail({ taskId }: { taskId: string }) {
  const { state: configState } = useServiceConfig();
  const interval =
    configState.kind === "ready"
      ? configState.config.task_poll_interval_seconds
      : FALLBACK_INTERVAL_SECONDS;

  const { status, segments, notice, error, loading, cancelling, refresh, cancel } =
    useTaskPolling(taskId, interval);
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  const running = status !== null && !isTerminal(status.status);
  const hasContent = segments.length > 0;

  // 标题只说「人话状态」；失败原因只出现在下面的提示块里，避免同一句话重复两遍
  const heading = !status
    ? loading
      ? "正在读取任务"
      : "任务状态未知"
    : status.status === "failed"
      ? "任务未完成"
      : status.status === "cancelled"
        ? "任务已取消"
        : status.stage_message;

  return (
    <section className="rounded-card border border-hairline bg-raised p-4 shadow-subtle sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-[20px] font-medium leading-snug text-ink">{heading}</h2>
        {status ? <StatusBadge status={status.status} /> : null}
      </div>

      {status ? (
        <>
          <div className="mt-4">
            <ProgressBar percent={status.progress_percent} label="转写进度" />
            <p className="tnum mt-2 text-[13px] leading-relaxed text-ink-soft">
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
            {running && status.estimated_remaining_seconds ? (
              <div className="flex gap-2">
                <dt>预计还需约</dt>
                <dd className="tnum text-ink">
                  {formatDuration(status.estimated_remaining_seconds)}
                </dd>
              </div>
            ) : null}
            {isTerminal(status.status) ? (
              <div className="flex gap-2">
                <dt>总耗时</dt>
                <dd className="tnum text-ink">{formatDuration(status.elapsed_seconds)}</dd>
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
            onClick={refresh}
            className="mt-2 rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-canvas"
          >
            重新加载
          </button>
        </div>
      ) : null}

      {status?.status === "failed" ? (
        <div className="mt-3 rounded-input bg-danger-soft px-3 py-3">
          <p className="text-[15px] text-danger">
            {status.error?.message ?? "任务未完成，请重试"}
          </p>
          <p className="mt-1 text-[14px] leading-relaxed text-ink-soft">
            {hasContent
              ? "已生成的内容保留在下方，但没有生成可下载文件。"
              : "没有生成任何内容，可以调整后重新发起任务。"}
          </p>
          <Link
            href="/"
            className="mt-3 inline-block rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-canvas"
          >
            重新发起任务
          </Link>
        </div>
      ) : null}

      {status?.status === "cancelled" ? (
        <div className="mt-3 rounded-input bg-warning-soft px-3 py-3">
          <p className="text-[14px] leading-relaxed text-warning">
            已生成的内容保留在下方，但没有生成可下载文件。需要完整逐字稿请重新发起任务。
          </p>
          <Link
            href="/"
            className="mt-3 inline-block rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-canvas"
          >
            重新发起任务
          </Link>
        </div>
      ) : null}

      <TranscriptViewer
        segments={segments}
        emptyHint={
          running ? "正在等待第一段内容…（首次转写需要先加载语音识别模型）" : "没有可展示的内容。"
        }
      />

      {running ? (
        <div className="mt-4">
          {confirmingCancel ? (
            <div className="flex flex-wrap items-center gap-3 rounded-input bg-sunken px-3 py-3">
              <p className="text-[14px] text-ink">
                确定取消吗？已生成的内容会保留，但不会生成可下载文件。
              </p>
              <button
                type="button"
                onClick={() => {
                  setConfirmingCancel(false);
                  void cancel();
                }}
                disabled={cancelling}
                className="rounded-button bg-ink-fill px-3 py-2 text-[14px] text-ink-inverse transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {cancelling ? "正在取消…" : "确认取消"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmingCancel(false)}
                className="rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-canvas"
              >
                继续等待
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={!status?.cancellable}
              className="rounded-button border border-danger px-3 py-2 text-[14px] text-danger transition-colors hover:bg-danger-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              取消任务
            </button>
          )}
        </div>
      ) : null}

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
