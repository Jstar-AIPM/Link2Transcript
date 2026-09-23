"use client";

/**
 * 任务轮询：状态 + 增量片段（4C 完整版）。
 *
 * 设计要点：
 * - **增量**：用 `after` 游标只取新片段，长内容不重复传输；
 * - **页面不可见时降频**：切到后台降到 10 秒一次，回来立刻拉一次
 *   （避免用户以为卡住，也避免后台空耗）；
 * - **网络中断与任务失败分开**：中断不清空内容、自动重试，连续失败 5 次才提示；
 * - **终态即停**：succeeded / failed / cancelled 之后不再轮询；
 * - **取消**：调用后端取消接口后立刻停止轮询，并把取消瞬间已落盘的内容补齐。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api/client";
import { isTerminal, type Segment, type TaskStatusResponse } from "@/lib/api/schemas";
import { rememberTask } from "@/lib/recent-task";

const MAX_POLL_FAILURES = 5;
/** 页面切到后台后的轮询间隔（秒） */
const HIDDEN_INTERVAL_SECONDS = 10;
/** 下界：即使后端配置很小也不低于 0.2 秒，避免打爆服务 */
const MIN_INTERVAL_MS = 200;

export type TaskPollingState = {
  status: TaskStatusResponse | null;
  segments: Segment[];
  notice: string | null;
  error: string | null;
  loading: boolean;
  cancelling: boolean;
  /** 任务不存在（链接抄错 / 记录被清理）：继续轮询没有意义 */
  missing: boolean;
};

export function useTaskPolling(taskId: string, intervalSeconds: number) {
  const [state, setState] = useState<TaskPollingState>({
    status: null,
    segments: [],
    notice: null,
    error: null,
    loading: true,
    cancelling: false,
    missing: false,
  });
  const afterRef = useRef(0);
  const stopRef = useRef(false);
  const failuresRef = useRef(0);
  const [hidden, setHidden] = useState(false);
  // 递增即重启轮询循环（重试入口用它，不用手改 stopRef）
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const onVisibility = () => setHidden(document.hidden);
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const effectiveIntervalMs = hidden
    ? Math.max(MIN_INTERVAL_MS, HIDDEN_INTERVAL_SECONDS * 1000)
    : Math.max(MIN_INTERVAL_MS, intervalSeconds * 1000);

  /** 拉一次状态与新增片段；返回状态，失败时抛错给调用方决定怎么表达 */
  const load = useCallback(async () => {
    const task = await api.getTask(taskId);
    const page = await api.getSegments(taskId, afterRef.current);
    // 兼容兜底：只接受序号不小于游标的片段。
    // 正常情况下后端不会重复返回，但界面是最后一道防线，不能让重复内容渲染两次。
    const fresh = page.segments.filter((segment) => segment.index >= afterRef.current);
    if (fresh.length) afterRef.current = Math.max(afterRef.current, page.next_after);
    rememberTask(taskId);
    setState((previous) => ({
      status: task,
      segments: fresh.length ? [...previous.segments, ...fresh] : previous.segments,
      notice: null,
      error: null,
      loading: false,
      cancelling: false,
      missing: false,
    }));
    failuresRef.current = 0;
    return task;
  }, [taskId]);

  const tick = useCallback(async () => {
    try {
      return await load();
    } catch (error) {
      // 任务不存在：链接抄错或记录已被清理。继续轮询无意义，直接告知用户。
      if (error instanceof ApiError && error.code === "TASK_NOT_FOUND") {
        failuresRef.current = MAX_POLL_FAILURES;
        setState((previous) => ({
          ...previous,
          loading: false,
          cancelling: false,
          missing: true,
          notice: null,
          error: null,
        }));
        return null;
      }
      const message = error instanceof ApiError ? error.message : "服务暂时不可用，请稍后重试";
      failuresRef.current += 1;
      const giveUp = failuresRef.current >= MAX_POLL_FAILURES;
      setState((previous) => ({
        ...previous,
        loading: false,
        cancelling: false,
        error: giveUp ? message : null,
        notice: giveUp
          ? null
          : `连接暂时中断，正在重试（${failuresRef.current}/${MAX_POLL_FAILURES}）`,
      }));
      return null;
    }
  }, [load]);

  useEffect(() => {
    stopRef.current = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (stopRef.current) return;
      const task = await tick();
      if (task && isTerminal(task.status)) return;
      if (failuresRef.current >= MAX_POLL_FAILURES) return;
      if (stopRef.current) return;
      timer = setTimeout(() => void loop(), effectiveIntervalMs);
    };

    void loop();
    return () => {
      stopRef.current = true;
      if (timer) clearTimeout(timer);
    };
  }, [tick, effectiveIntervalMs, generation]);

  /** 手动重试（网络恢复后 / 失败提示条上的「重新加载」）：重启轮询循环 */
  const refresh = useCallback(() => {
    failuresRef.current = 0;
    stopRef.current = true;
    setState((previous) => ({ ...previous, error: null, notice: null, loading: true }));
    setGeneration((value) => value + 1);
  }, []);

  /** 取消任务：立即停止轮询，并把取消瞬间已落盘的内容补齐（磁盘才是权威） */
  const cancel = useCallback(async () => {
    setState((previous) => ({ ...previous, cancelling: true }));
    try {
      const task = await api.cancelTask(taskId);
      stopRef.current = true;
      const page = await api.getSegments(taskId, afterRef.current);
      const fresh = page.segments.filter((segment) => segment.index >= afterRef.current);
      if (fresh.length) afterRef.current = Math.max(afterRef.current, page.next_after);
      setState((previous) => ({
        status: task,
        segments: fresh.length ? [...previous.segments, ...fresh] : previous.segments,
        notice: null,
        error: null,
        loading: false,
        cancelling: false,
        missing: false,
      }));
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "取消失败，请稍后重试";
      setState((previous) => ({ ...previous, cancelling: false, error: message }));
    }
  }, [taskId]);

  return { ...state, refresh, cancel };
}
