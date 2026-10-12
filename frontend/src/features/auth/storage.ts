"use client";

/**
 * 邀请码登录态（localStorage）+ 可订阅快照。
 *
 * 为什么用 localStorage：邀请制阶段没有账号体系，令牌就是身份；同一台设备
 * 不该反复输码（每次输码都会消耗一次名额）。退出按钮会显式清除。
 *
 * 为什么用外部快照（`useSyncExternalStore`）：登录态同时被门禁页、顶栏、
 * 接口客户端读取，用一份带订阅的快照可以保证"登录/退出后各处同步刷新"，
 * 并且服务端快照固定为空，避免 hydration 不一致。
 */
import { inviteStatusSchema, type InviteStatus } from "@/lib/api/schemas";

const TOKEN_KEY = "transcript-extractor.token";
const STATUS_KEY = "transcript-extractor.status";

export type AuthSnapshot = {
  token: string | null;
  status: InviteStatus | null;
  /** 被动登出的原因（如令牌失效），由门禁页直接展示 */
  notice: string | null;
};

const EMPTY: AuthSnapshot = { token: null, status: null, notice: null };

let cached: AuthSnapshot | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function readStatus(raw: string | null): InviteStatus | null {
  if (!raw) return null;
  try {
    const parsed = inviteStatusSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

function read(): AuthSnapshot {
  try {
    if (typeof window === "undefined") return EMPTY;
    const token = window.localStorage.getItem(TOKEN_KEY);
    if (!token) return EMPTY;
    return { token, status: readStatus(window.localStorage.getItem(STATUS_KEY)), notice: null };
  } catch {
    // 隐私模式 / 受限环境下 localStorage 可能不可用：视为未登录
    return EMPTY;
  }
}

/** 客户端快照（引用稳定，供 useSyncExternalStore 使用）。 */
export function getAuthSnapshot(): AuthSnapshot {
  if (cached === null) cached = read();
  return cached;
}

/** 服务端没有 localStorage → 视为未登录。 */
export function getServerAuthSnapshot(): AuthSnapshot {
  return EMPTY;
}

/** 便捷读取：接口客户端与下载都用它取令牌。 */
export function readToken(): string {
  return getAuthSnapshot().token ?? "";
}

export function saveAuth(token: string, status: InviteStatus): void {
  cached = { token, status, notice: null };
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(STATUS_KEY, JSON.stringify(status));
  } catch {
    // 隐私模式下 localStorage 不可用；此时登录仅在当前页面内有效
  }
  emit();
}

export function updateStatus(status: InviteStatus): void {
  const current = getAuthSnapshot();
  if (!current.token) return;
  cached = { token: current.token, status, notice: null };
  try {
    window.localStorage.setItem(STATUS_KEY, JSON.stringify(status));
  } catch {
    /* 忽略 */
  }
  emit();
}

export function clearAuth(notice?: string): void {
  cached = { token: null, status: null, notice: notice ?? null };
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(STATUS_KEY);
  } catch {
    /* 忽略 */
  }
  emit();
}
