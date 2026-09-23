"use client";

/** 服务不可用提示：中文原因 + 怎么做 + 重试。结构固定（设计基调 §3）。 */
export function ServiceUnavailable({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-card bg-danger-soft p-4">
      <p className="text-[15px] text-danger">{message}</p>
      <p className="mt-1 text-[14px] leading-relaxed text-ink-soft">
        请确认后端服务已启动：在项目根目录运行
        <code className="mx-1 rounded-chip bg-canvas px-2 py-0.5 text-[13px]">
          uvicorn backend.app.main:app --port 8000
        </code>
        ，然后点下面的按钮重试。
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-canvas"
      >
        重新检查
      </button>
    </div>
  );
}
