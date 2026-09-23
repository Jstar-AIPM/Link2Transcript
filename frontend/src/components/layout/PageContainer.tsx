import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** 内容栏：1120px + 两侧 56px 留白（窄屏自动收），与 03 工具一致。 */
export function PageContainer({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("mx-auto w-full max-w-page px-14 max-[900px]:px-8 max-[640px]:px-[22px]", className)}>
      {children}
    </div>
  );
}
