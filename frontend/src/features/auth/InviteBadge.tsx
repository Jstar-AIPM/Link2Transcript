"use client";

/** 顶栏右侧的登录态：邀请码掩码 + 剩余次数 + 「邀请码管理」（仅管理员）+ 退出。 */
import Link from "next/link";
import { useSyncExternalStore } from "react";

import { Button, buttonClass } from "@/components/ui/Button";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuth, clearAuth } from "@/features/auth/storage";

export function InviteBadge() {
  const { token, status } = useSyncExternalStore(
    subscribeAuth,
    getAuthSnapshot,
    getServerAuthSnapshot,
  );

  if (!token || !status) return null;

  const quota =
    status.unlimited || status.remaining_uses === null
      ? "不限次数"
      : `剩余 ${status.remaining_uses} 次`;
  const expiry = status.expires_at ? ` · ${status.expires_at.slice(0, 10)} 到期` : "";

  return (
    <div className="flex items-center gap-1.5">
      {status.is_admin ? (
        <Link
          href="/admin"
          className={buttonClass("quiet", "h-8 text-[12.5px] max-[640px]:hidden")}
        >
          邀请码管理
        </Link>
      ) : null}
      <span
        className="font-mono text-[11px] text-muted max-[900px]:hidden"
        title={`邀请码 ${status.code_masked}${expiry}`}
      >
        {status.code_masked} · {quota}
      </span>
      <Button variant="quiet" className="h-8 text-[12.5px]" onClick={() => clearAuth()}>
        退出
      </Button>
    </div>
  );
}
