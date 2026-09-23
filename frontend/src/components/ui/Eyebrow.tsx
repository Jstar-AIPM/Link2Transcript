import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** 等宽眉标：11px 大写 + 宽字距，承担"标签"角色。 */
export function Eyebrow({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("font-mono text-[11px] uppercase tracking-[0.08em] text-muted", className)} {...rest} />
  );
}
