"use client";

/** 模式切换：两个药丸按钮（设计基调 §3 的 Pill Chip）。 */
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
            className={[
              "rounded-chip px-3 py-2 text-[14px] transition-colors disabled:cursor-not-allowed disabled:opacity-60",
              selected
                ? "bg-accent text-ink-inverse"
                : "border border-hairline text-ink-soft hover:bg-raised",
            ].join(" ")}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
