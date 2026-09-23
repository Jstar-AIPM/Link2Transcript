import { cn } from "@/lib/utils";

type Props = {
  /** 填充比例（0–100） */
  percent: number;
  className?: string;
  label?: string;
  /** 读屏软件用的兜底描述（如「已转写 42分18秒，共 1小时」） */
  valueText?: string;
};

/** 进度条：hairline 轨道 + 强调色填充（与 03 工具的数据条同构）。 */
export function Bar({ percent, className, label, valueText }: Props) {
  const width = Math.max(0, Math.min(100, percent));
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-pill bg-sunken", className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(width)}
      aria-valuetext={valueText}
      aria-label={label ?? `进度 ${Math.round(width)}%`}
    >
      <div
        className="h-full rounded-pill bg-accent transition-[width] duration-300 ease-out"
        style={{ width: `${width}%` }}
      />
    </div>
  );
}
