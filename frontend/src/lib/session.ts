"use client";

/**
 * 会话（邀请码登录后拿到的 token）的本地存储。
 *
 * 为什么放 localStorage：本阶段没有账号体系，token 就是"身份"。
 * 它随浏览器持久化（有效期 30 天），因此刷新/重开页面仍能继续查看任务；
 * 清空浏览器数据等于退出登录（任务也随之看不到），这是邀请制阶段的已知取舍。
 */
const TOKEN_KEY = "transcript-extractor.token";
const ADMIN_KEY = "transcript-extractor.is-admin";

export function saveToken(token: string, isAdmin: boolean): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(ADMIN_KEY, isAdmin ? "1" : "0");
  } catch {
    // 隐私模式下 localStorage 不可用；此时登录仅在当前页面内有效
  }
}

export function readToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function isAdmin(): boolean {
  try {
    return window.localStorage.getItem(ADMIN_KEY) === "1";
  } catch {
    return false;
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(ADMIN_KEY);
  } catch {
    /* 忽略 */
  }
}
