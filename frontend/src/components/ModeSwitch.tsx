"use client";

import { cn } from "@/lib/utils";

/** 模式切换：药丸标签（激活态用强调色浅底 + 强调色文字，与 03 工具一致）。 */
export function ModeSwitch<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div role="tablist" aria-label="任务来源" className="flex flex-wrap gap-2">
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-pill px-3.5 py-1.5 text-[13.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-45",
              selected
                ? "bg-accent-wash font-semibold text-accent"
                : "border border-line text-muted hover:bg-sunken hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
