"use client";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

/** 服务不可用提示：中文原因 + 怎么做 + 重试。结构固定。 */
export function ServiceUnavailable({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-l-2 border-l-danger">
      <p className="text-[15px] font-medium text-danger">{message}</p>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-muted">
        请确认后端服务已启动：在项目根目录运行
        <code className="mx-1 break-anywhere rounded-control bg-sunken px-1.5 py-0.5 font-mono text-[12.5px] text-ink">
          uvicorn backend.app.main:app --port 8000
        </code>
        ，然后点下面的按钮重试。
      </p>
      <Button variant="ghost" onClick={onRetry} className="mt-4 h-9 text-[13.5px]">
        重新检查
      </Button>
    </Card>
  );
}
