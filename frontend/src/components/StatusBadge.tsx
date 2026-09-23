import type { TaskStatus } from "@/lib/api/schemas";

import { Badge, type BadgeTone } from "@/components/ui/Badge";

/**
 * 状态徽标：颜色 + 文字同时表达状态（颜色永远不是唯一信号）。
 * 四类文案固定为：进行中 / 已完成 / 未完成 / 已取消。
 */
const MAP: Record<TaskStatus, { text: string; tone: BadgeTone }> = {
  pending: { text: "进行中", tone: "neutral" },
  validating: { text: "进行中", tone: "active" },
  checking_subtitle: { text: "进行中", tone: "active" },
  downloading_audio: { text: "进行中", tone: "active" },
  extracting_audio: { text: "进行中", tone: "active" },
  transcribing: { text: "进行中", tone: "active" },
  exporting: { text: "进行中", tone: "active" },
  succeeded: { text: "已完成", tone: "success" },
  failed: { text: "未完成", tone: "danger" },
  cancelled: { text: "已取消", tone: "warn" },
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  const { text, tone } = MAP[status];
  return <Badge tone={tone}>{text}</Badge>;
}
