"use client";

/**
 * 后端连通性自检（4A 骨架的一部分）。
 *
 * 它存在的意义不是「展示配置」，而是证明三件事已经接好：
 * 1. 同源反向代理生效（浏览器请求 /api/v1/config，由 Next 转发到 FastAPI）；
 * 2. zod 运行时校验生效（字段结构不对会走「无法识别」分支）；
 * 3. 错误文案统一来自后端 / 前端兜底，且都是中文。
 */
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import type { PublicConfig } from "@/lib/api/schemas";

type State =
  | { kind: "loading" }
  | { kind: "ready"; config: PublicConfig }
  | { kind: "failed"; message: string };

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "服务暂时不可用，请稍后重试";
}

export function BackendCheck() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // 所有 setState 都发生在 await 之后：既符合 React 的副作用规则，
    // 也避免「组件已卸载还写状态」。
    void (async () => {
      try {
        const config = await api.getConfig();
        if (!cancelled) setState({ kind: "ready", config });
      } catch (error) {
        if (!cancelled) setState({ kind: "failed", message: messageOf(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  return (
    <section className="rounded-card border border-hairline bg-raised p-4 shadow-subtle sm:p-5">
      <header className="flex items-center justify-between gap-3">
        <h2 className="text-[15px] font-medium text-ink">服务连接</h2>
        {state.kind === "ready" ? (
          <span className="rounded-chip bg-success-soft px-2.5 py-1 text-[12px] text-success">
            已连接
          </span>
        ) : null}
        {state.kind === "failed" ? (
          <span className="rounded-chip bg-danger-soft px-2.5 py-1 text-[12px] text-danger">
            未连接
          </span>
        ) : null}
        {state.kind === "loading" ? (
          <span className="rounded-chip bg-neutral-status-soft px-2.5 py-1 text-[12px] text-neutral-status">
            检查中
          </span>
        ) : null}
      </header>

      {state.kind === "ready" ? (
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-[14px] sm:grid-cols-3">
          <div>
            <dt className="text-ink-soft">单文件上传上限</dt>
            <dd className="tnum text-ink">{state.config.max_upload_mb} MB</dd>
          </div>
          <div>
            <dt className="text-ink-soft">进度刷新间隔</dt>
            <dd className="tnum text-ink">{state.config.task_poll_interval_seconds} 秒</dd>
          </div>
          <div>
            <dt className="text-ink-soft">单条内容时长上限</dt>
            <dd className="tnum text-ink">
              {state.config.max_media_minutes > 0
                ? `${state.config.max_media_minutes} 分钟`
                : "不限制"}
            </dd>
          </div>
        </dl>
      ) : null}

      {state.kind === "failed" ? (
        <div className="mt-3 rounded-input bg-danger-soft p-3">
          <p className="text-[14px] text-danger">{state.message}</p>
          <p className="mt-1 text-[13px] text-ink-soft">
            请先确认后端已启动（在项目根目录运行 uvicorn），再点下面的按钮重试。
          </p>
          <button
            type="button"
            onClick={() => {
              setState({ kind: "loading" });
              setAttempt((value) => value + 1);
            }}
            className="mt-3 rounded-button border border-hairline-strong px-3 py-2 text-[14px] text-ink transition-colors hover:bg-sunken"
          >
            重新检查
          </button>
        </div>
      ) : null}
    </section>
  );
}
