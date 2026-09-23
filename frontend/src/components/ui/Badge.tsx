import { cn } from "@/lib/utils";

export type BadgeTone = "neutral" | "active" | "success" | "danger" | "warn";

/** 状态徽标：颜色 + 文字同时表达状态（颜色永远不是唯一信号）。 */
const TONES: Record<BadgeTone, string> = {
  neutral: "bg-sunken text-ink",
  active: "bg-accent-wash text-accent",
  success: "bg-sunken text-success",
  danger: "bg-sunken text-danger",
  warn: "bg-sunken text-warn",
};

export function Badge({
  tone = "neutral",
  className,
  ...rest
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-pill px-2.5 py-0.5 font-mono text-xs",
        TONES[tone],
        className,
      )}
      {...rest}
    />
  );
}
