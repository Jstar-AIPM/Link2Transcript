"use client";

/**
 * 任务详情（完整体验版）。
 *
 * 页面由后端状态驱动，七种状态各有明确表现；三件关键事在这里落地：
 * 1. 过程可见：阶段文案 + 进度条 + 逐段追加 + 自动跟随可暂停；
 * 2. 可控：取消任务（二次确认，取消后内容保留、无下载入口）；
 * 3. 不丢：刷新后从磁盘重放（游标归零重新拉），内容与刷新前逐字一致。
 */
import Link from "next/link";
import { useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptViewer } from "@/components/TranscriptViewer";
import { Bar } from "@/components/ui/Bar";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { useServiceConfig } from "@/features/service-config/useServiceConfig";
import { downloadArtifact } from "@/lib/api/download";
import { useTaskPolling } from "@/features/task-detail/useTaskPolling";
import { formatDuration, formatPercent } from "@/lib/format";
import { isTerminal, type TaskStatusResponse } from "@/lib/api/schemas";

const FALLBACK_INTERVAL_SECONDS = 2;

function Fact({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className={mono ? "font-mono text-[13px] text-body" : "text-[13px] text-body"}>{value}</dd>
    </div>
  );
}

/**
 * 空状态文案：必须看清"当前在哪一步"再说，否则会出现
 * "正在检查字幕，首次转写需要先加载语音识别模型" 这种前后矛盾的提示。
 *
 * - 检查字幕 / 下载音频 / 提取音频：还在准备音频，与模型无关；
 * - 转写中且还没有片段：这里才是真正的"首字延迟"，需要说明模型加载。
 */
function emptyHint(status: TaskStatusResponse | null): string {
  if (!status) return "正在等待任务状态…";
  if (isTerminal(status.status)) return "没有可展示的内容。";
  switch (status.status) {
    case "downloading_audio":
      return "正在准备音频…（先从视频里取出音轨，还没有文字可显示）";
    case "extracting_audio":
      return "正在准备音频…（正在从视频中提取音轨）";
    case "exporting":
      return "正在生成文件…";
    case "transcribing":
      return "正在等待第一段内容…（首次处理需要约 1–2 分钟，之后会快很多）";
    default:
      return "正在检查视频字幕…（有字幕会直接提取，通常几秒完成）";
  }
}

export function TaskDetail({ taskId }: { taskId: string }) {
  const { state: configState } = useServiceConfig();
  const interval =
    configState.kind === "ready"
      ? configState.config.task_poll_interval_seconds
      : FALLBACK_INTERVAL_SECONDS;

  const { status, segments, notice, error, loading, cancelling, missing, refresh, cancel } =
    useTaskPolling(taskId, interval);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "ok" | "fail">("idle");
  const [downloadError, setDownloadError] = useState<string | null>(null);

  /** 带鉴权下载：令牌放在请求头里，不放进 URL */
  const saveArtifact = async (url: string, filename: string) => {
    setDownloadError(null);
    try {
      await downloadArtifact(url, filename);
    } catch {
      setDownloadError("下载失败，请稍后重试");
    }
  };

  /** 复制全文：只在用户点击时才读剪贴板 API（无权限/不支持时给中文提示） */
  const copyTranscript = async () => {
    const text = segments.map((segment) => segment.text).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("ok");
    } catch {
      setCopyState("fail");
    }
    window.setTimeout(() => setCopyState("idle"), 2500);
  };

  if (missing) {
    return (
      <Card className="p-7">
        <Eyebrow className="mb-2">未找到</Eyebrow>
        <h2 className="text-[22px] font-semibold tracking-[-0.01em]">没有找到这个任务</h2>
        <p className="mt-2 max-w-[52ch] text-[14.5px] leading-relaxed text-muted">
          可能是地址抄错了，或者这个任务的记录已经被清理。任务记录默认会一直保留，
          你可以从首页重新发起任务。
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Link href="/" className={buttonClass("primary")}>
            返回首页，发起新任务
          </Link>
          <Button variant="ghost" onClick={refresh} className="h-10 text-[13.5px]">
            重新检查
          </Button>
        </div>
        <p className="mt-5 break-anywhere font-mono text-[12px] text-faint">任务编号：{taskId}</p>
      </Card>
    );
  }

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
    <Card className="p-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 aria-live="polite" className="text-[22px] font-semibold tracking-[-0.01em]">
          {heading}
        </h1>
        {status ? <StatusBadge status={status.status} /> : null}
      </div>

      {status ? (
        <>
          <div className="mt-5">
            <Bar
              percent={status.progress_percent}
              label="转写进度"
              valueText={
                status.media_duration_seconds
                  ? `已转写 ${formatDuration(status.transcribed_seconds)}，共 ${formatDuration(status.media_duration_seconds)}`
                  : undefined
              }
            />
            <p className="mt-2 font-mono text-[12.5px] text-muted">
              {status.media_duration_seconds
                ? `已转写 ${formatDuration(status.transcribed_seconds)} / 共 ${formatDuration(status.media_duration_seconds)}`
                : "正在准备音频"}
              {status.segment_count > 0 ? ` · 已生成 ${status.segment_count} 段` : ""}
              {` · 进度 ${formatPercent(status.progress_percent)}`}
            </p>
          </div>

          <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-1.5 sm:grid-cols-2">
            <Fact label="处理方式" value={status.processing_method_label} mono={false} />
            {status.media_duration_seconds ? (
              <Fact label="内容时长" value={formatDuration(status.media_duration_seconds)} />
            ) : null}
            {running && status.estimated_remaining_seconds ? (
              <Fact label="预计还需约" value={formatDuration(status.estimated_remaining_seconds)} />
            ) : null}
            {isTerminal(status.status) ? (
              <Fact label="总耗时" value={formatDuration(status.elapsed_seconds)} />
            ) : null}
          </dl>
        </>
      ) : null}

      {notice ? (
        <p className="mt-4 border-l-2 border-line-strong pl-3 text-[13.5px] text-muted">{notice}</p>
      ) : null}

      {error ? (
        <div className="mt-4 border-l-2 border-danger pl-3">
          <p className="text-[14px] text-danger">{error}</p>
          <Button variant="ghost" onClick={refresh} className="mt-3 h-9 text-[13.5px]">
            重新加载
          </Button>
        </div>
      ) : null}

      {status?.status === "failed" ? (
        <div className="mt-4 border-l-2 border-danger pl-3">
          <p className="text-[15px] font-medium text-danger">
            {status.error?.message ?? "任务未完成，请重试"}
          </p>
          <p className="mt-1 text-[13.5px] leading-relaxed text-muted">
            {hasContent
              ? "已生成的内容保留在下方，但没有生成可下载文件。"
              : "没有生成任何内容，可以调整后重新发起任务。"}
          </p>
          <Link href="/" className={buttonClass("ghost", "mt-3 h-9 text-[13.5px]")}>
            重新发起任务
          </Link>
        </div>
      ) : null}

      {status?.status === "cancelled" ? (
        <div className="mt-4 border-l-2 border-warn pl-3">
          <p className="text-[13.5px] leading-relaxed text-body">
            已生成的内容保留在下方，但没有生成可下载文件。需要完整逐字稿请重新发起任务。
          </p>
          <Link href="/" className={buttonClass("ghost", "mt-3 h-9 text-[13.5px]")}>
            重新发起任务
          </Link>
        </div>
      ) : null}

      <TranscriptViewer
        segments={segments}
        total={status?.segment_count ?? segments.length}
        emptyHint={emptyHint(status)}
        headerActions={
          hasContent ? (
            <button
              type="button"
              onClick={() => void copyTranscript()}
              className="h-9 rounded-control border border-line-strong px-3 text-[13px] text-ink transition-colors hover:bg-sunken"
            >
              {copyState === "ok"
                ? "已复制"
                : copyState === "fail"
                  ? "复制失败，请手动选择"
                  : "复制全文"}
            </button>
          ) : null
        }
      />

      {running ? (
        <div className="mt-5">
          {confirmingCancel ? (
            <div className="flex flex-wrap items-center gap-3 rounded-control bg-sunken px-4 py-3">
              <p className="text-[13.5px]">
                确定取消吗？已生成的内容会保留，但不会生成可下载文件。
              </p>
              <Button
                onClick={() => {
                  setConfirmingCancel(false);
                  void cancel();
                }}
                disabled={cancelling}
                className="h-9 text-[13.5px]"
              >
                {cancelling ? "正在取消…" : "确认取消"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => setConfirmingCancel(false)}
                className="h-9 text-[13.5px]"
              >
                继续等待
              </Button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmingCancel(true)}
              disabled={!status?.cancellable}
              className="h-9 rounded-control border border-line-strong px-3.5 text-[13.5px] text-muted transition-colors hover:bg-sunken hover:text-danger disabled:cursor-not-allowed disabled:opacity-45"
            >
              取消任务
            </button>
          )}
        </div>
      ) : null}

      {status?.status === "succeeded" && status.artifacts.markdown ? (
        <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
          <Button
            onClick={() => void saveArtifact(status.artifacts.markdown as string, "transcript.md")}
          >
            下载 Markdown
          </Button>
          {status.artifacts.txt ? (
            <Button
              variant="ghost"
              onClick={() => void saveArtifact(status.artifacts.txt as string, "transcript.txt")}
            >
              下载 TXT
            </Button>
          ) : null}
          {downloadError ? (
            <span className="text-[13px] text-danger">{downloadError}</span>
          ) : null}
        </div>
      ) : null}

      <p className="mt-6 text-[13px] text-muted">
        <Link href="/" className="underline underline-offset-2 hover:text-ink">
          返回首页，发起新任务
        </Link>
      </p>
    </Card>
  );
}
