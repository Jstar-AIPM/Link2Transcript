"use client";

/**
 * 任务轮询：状态 + 增量片段。
 *
 * 4B 版本只保证「真实、不丢、能走到终态」；4C 会在这一层之上补：
 * 自动跟随可暂停、取消任务、未完成标注、失败后的下一步动作。
 *
 * 三个必须分开的「不好看的状态」（设计基调 §4）：
 * 1. 任务失败（后端 failed / cancelled）—— 内容是可信的，只是没完成；
 * 2. 网络中断 —— 内容不清空，提示正在重试；
 * 3. 还什么都没有 —— 显示「正在等待第一段内容」，不是空白。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import { isTerminal, type Segment, type TaskStatusResponse } from "@/lib/api/schemas";

const MAX_POLL_FAILURES = 5;

export type TaskPollingState = {
  status: TaskStatusResponse | null;
  segments: Segment[];
  notice: string | null;
  error: string | null;
  loading: boolean;
};

export function useTaskPolling(taskId: string, intervalSeconds: number) {
  const [state, setState] = useState<TaskPollingState>({
    status: null,
    segments: [],
    notice: null,
    error: null,
    loading: true,
  });
  const afterRef = useRef(0);
  const stopRef = useRef(false);
  const failuresRef = useRef(0);

  const load = useCallback(async () => {
    try {
      const task = await api.getTask(taskId);
      const page = await api.getSegments(taskId, afterRef.current);
      setState((previous) => ({
        status: task,
        segments: page.segments.length
          ? [...previous.segments, ...page.segments]
          : previous.segments,
        notice: null,
        error: null,
        loading: false,
      }));
      if (page.segments.length) afterRef.current = page.next_after;
      failuresRef.current = 0;
      return task;
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "服务暂时不可用，请稍后重试";
      failuresRef.current += 1;
      const giveUp = failuresRef.current >= MAX_POLL_FAILURES;
      setState((previous) => ({
        ...previous,
        loading: false,
        error: giveUp ? message : null,
        notice: giveUp ? null : `连接暂时中断，正在重试（${failuresRef.current}/${MAX_POLL_FAILURES}）`,
      }));
      throw error;
    }
  }, [taskId]);

  useEffect(() => {
    stopRef.current = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (stopRef.current) return;
      try {
        const task = await load();
        if (isTerminal(task.status)) return;
      } catch {
        if (failuresRef.current >= MAX_POLL_FAILURES) return;
      }
      if (stopRef.current) return;
      timer = setTimeout(() => void tick(), Math.max(1000, intervalSeconds * 1000));
    };

    void tick();
    return () => {
      stopRef.current = true;
      if (timer) clearTimeout(timer);
    };
  }, [load, intervalSeconds]);

  /** 终态后用户点「刷新」时手动拉一次（也是失败后重试的入口） */
  const refresh = useCallback(async () => {
    failuresRef.current = 0;
    setState((previous) => ({ ...previous, error: null, notice: null, loading: true }));
    try {
      await load();
    } catch {
      /* 错误状态已经在 load 里写好了 */
    }
  }, [load]);

  return { ...state, refresh };
}
