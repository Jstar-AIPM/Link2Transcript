"use client";

/**
 * 需要登录才能看到的内容的包装：未登录时跳登录页，不渲染子内容。
 *
 * 为什么要有它：只在"提交时"才发现未登录，用户会先填一堆东西再被弹回去。
 * 提前拦掉，体验更好，也避免无谓的请求。
 */
import type { ReactNode } from "react";

import { useRequireLogin } from "@/features/auth/useRequireLogin";

export function HomeGate({ children }: { children: ReactNode }) {
  const { ready, needLogin } = useRequireLogin();
  if (needLogin) {
    return <p className="text-[14px] text-muted">正在跳转到登录页…</p>;
  }
  if (!ready) {
    return <p className="text-[14px] text-muted">正在读取服务配置…</p>;
  }
  return <>{children}</>;
}
