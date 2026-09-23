import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** 卡片：只用 1px hairline 描边立起来，不加任何投影（与 03 工具一致）。 */
export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-card border border-line bg-surface p-5", className)} {...rest} />;
}
