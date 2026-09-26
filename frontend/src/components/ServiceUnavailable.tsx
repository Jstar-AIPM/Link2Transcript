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
        可能是服务正在重启，或网络暂时不通。稍等片刻后点下面的按钮重试即可。
      </p>
      <Button variant="ghost" onClick={onRetry} className="mt-4 h-9 text-[13.5px]">
        重新检查
      </Button>
    </Card>
  );
}
