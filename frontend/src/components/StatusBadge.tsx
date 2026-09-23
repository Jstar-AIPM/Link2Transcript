"use client";

import type { TaskStatus } from "@/lib/api/schemas";

/**
 * 状态徽标：颜色 + 文字同时表达状态（颜色永远不是唯一信号）。
 * 四类文案固定为：进行中 / 已完成 / 未完成 / 已取消。
 */
const LABELS: Record<TaskStatus, { text: string; className: string }> = {
  pending: { text: "进行中", className: "bg-neutral-status-soft text-neutral-status" },
  validating: { text: "进行中", className: "bg-accent-soft text-accent" },
  checking_subtitle: { text: "进行中", className: "bg-accent-soft text-accent" },
  downloading_audio: { text: "进行中", className: "bg-accent-soft text-accent" },
  extracting_audio: { text: "进行中", className: "bg-accent-soft text-accent" },
  transcribing: { text: "进行中", className: "bg-accent-soft text-accent" },
  exporting: { text: "进行中", className: "bg-accent-soft text-accent" },
  succeeded: { text: "已完成", className: "bg-success-soft text-success" },
  failed: { text: "未完成", className: "bg-danger-soft text-danger" },
  cancelled: { text: "已取消", className: "bg-warning-soft text-warning" },
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  const { text, className } = LABELS[status];
  return (
    <span className={`rounded-chip px-2.5 py-1 text-[12px] ${className}`}>{text}</span>
  );
}
