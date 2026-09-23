"use client";

/**
 * 「最近任务」：首页记住最近一次任务，方便回头查看。
 *
 * 本阶段范围明确不做历史任务列表（那需要后端列表接口与清理语义），
 * 但**恢复最近一个任务**是必须的 —— 否则用户刷新或关掉页面后，
 * 只能靠手抄 URL 回来，这正是「以为链接失效」的常见来源。
 */
const LAST_TASK_KEY = "transcript-extractor.last-task";

export type RecentTask = { taskId: string; at: number };

export function rememberTask(taskId: string): void {
  try {
    window.localStorage.setItem(LAST_TASK_KEY, JSON.stringify({ taskId, at: Date.now() }));
  } catch {
    // 隐私模式下 localStorage 可能不可用；记住与否不影响主流程
  }
}

export function readRecentTask(): RecentTask | null {
  try {
    const raw = window.localStorage.getItem(LAST_TASK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<RecentTask>;
    if (!parsed || typeof parsed.taskId !== "string") return null;
    return { taskId: parsed.taskId, at: typeof parsed.at === "number" ? parsed.at : 0 };
  } catch {
    return null;
  }
}
