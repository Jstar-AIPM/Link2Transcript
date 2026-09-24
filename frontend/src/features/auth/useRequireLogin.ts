"use client";

/**
 * 登录守卫：线上（配置要求登录）且本地没有令牌时，跳转到登录页。
 *
 * 用 `replace` 而不是 `push`：用户从登录页回来时不应该出现"再点一次返回又跳走"的死循环。
 */
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useServiceConfig } from "@/features/service-config/useServiceConfig";
import { readToken } from "@/lib/session";

export function useRequireLogin(): { ready: boolean; needLogin: boolean } {
  const router = useRouter();
  const { state } = useServiceConfig();
  const needLogin = state.kind === "ready" && state.config.require_auth && !readToken();

  useEffect(() => {
    if (needLogin) router.replace("/login");
  }, [needLogin, router]);

  return { ready: state.kind === "ready", needLogin };
}
