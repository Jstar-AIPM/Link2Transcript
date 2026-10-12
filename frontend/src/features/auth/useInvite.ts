"use client";

/**
 * 邀请码门禁的状态机：loading →（locked ⇄ unlocked）。
 *
 * 带着令牌打开页面时先校验一次 `/auth/me`：拿到最新剩余次数就更新；
 * 令牌已失效（服务端换过签名密钥、已过期、被停用）则退出登录并给出原因。
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { getAuthSnapshot, getServerAuthSnapshot, saveAuth, subscribeAuth, clearAuth, updateStatus } from "@/features/auth/storage";
import type { InviteStatus } from "@/features/auth/types";
import { ApiError, api } from "@/lib/api/client";

export type InvitePhase = "loading" | "locked" | "unlocked";

const noopSubscribe = () => () => {};

/** 从登录/续期响应里取出状态部分（去掉 token）。 */
export function statusOf(response: InviteStatus & { token: string }): InviteStatus {
  return {
    code_masked: response.code_masked,
    is_admin: response.is_admin,
    unlimited: response.unlimited,
    remaining_uses: response.remaining_uses,
    expires_at: response.expires_at,
  };
}

export function useInvite(): {
  phase: InvitePhase;
  status: InviteStatus | null;
  error: string | null;
  submitting: boolean;
  submit: (code: string) => Promise<boolean>;
  signOut: () => void;
} {
  const { token, status, notice } = useSyncExternalStore(
    subscribeAuth,
    getAuthSnapshot,
    getServerAuthSnapshot,
  );
  const hydrated = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );

  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    void api
      .me()
      .then((fresh) => {
        if (alive) updateStatus(statusOf(fresh));
      })
      .catch((caught) => {
        // 只有"确实没权限/已过期"才踢出登录；网络抖动不登出
        if (alive && caught instanceof ApiError && caught.status === 401) {
          clearAuth("登录状态已失效，请重新输入邀请码。");
        }
      });
    return () => {
      alive = false;
    };
  }, [token]);

  const submit = useCallback(async (code: string) => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.login(code);
      saveAuth(result.token, statusOf(result));
      return true;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "服务暂时不可用，请稍后重试");
      return false;
    } finally {
      setSubmitting(false);
    }
  }, []);

  const signOut = useCallback(() => {
    clearAuth();
    setError(null);
  }, []);

  const phase: InvitePhase = !hydrated ? "loading" : token ? "unlocked" : "locked";

  return { phase, status, error: error ?? notice, submitting, submit, signOut };
}
