"use client";

/** 进度条：语义化 role=progressbar，颜色不用来单独表达状态（旁边总有文字）。 */
export function ProgressBar({
  percent,
  label,
  tone = "accent",
}: {
  percent: number;
  label?: string;
  tone?: "accent" | "muted";
}) {
  const clamped = Math.max(0, Math.min(100, percent));
  const fill = tone === "accent" ? "bg-accent" : "bg-hairline-strong";
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
      aria-label={label ?? "进度"}
      className="h-1.5 w-full overflow-hidden rounded-chip bg-sunken"
    >
      <div
        className={`h-full rounded-chip ${fill} transition-[width] duration-300 ease-out`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
