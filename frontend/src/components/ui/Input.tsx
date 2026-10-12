import type { InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** 文本输入框：与按钮同一套控件圆角与描边（对齐 03 工具）。 */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-control border border-line-strong bg-canvas px-3 text-[15px] text-ink",
        "placeholder:text-faint disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...rest}
    />
  );
}
