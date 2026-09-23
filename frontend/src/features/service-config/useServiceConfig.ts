"use client";

/**
 * 读取后端公开配置（上传上限、轮询间隔、时长上限）。
 *
 * 三态必须分开（设计基调 §4）：读取中 / 读到了 / 读不到。
 * 读不到时不能假装有默认值继续跑 —— 那样用户会在提交后才发现限制，
 * 所以调用方要显式处理 failed 状态（给出中文原因与重试入口）。
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import type { PublicConfig } from "@/lib/api/schemas";

export type ServiceConfigState =
  | { kind: "loading" }
  | { kind: "ready"; config: PublicConfig }
  | { kind: "failed"; message: string };

export function useServiceConfig() {
  const [state, setState] = useState<ServiceConfigState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const config = await api.getConfig();
        if (!cancelled) setState({ kind: "ready", config });
      } catch (error) {
        const message =
          error instanceof ApiError ? error.message : "服务暂时不可用，请稍后重试";
        if (!cancelled) setState({ kind: "failed", message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const reload = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  return { state, reload };
}
