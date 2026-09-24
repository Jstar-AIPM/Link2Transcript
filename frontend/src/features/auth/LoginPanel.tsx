"use client";

/**
 * 邀请码登录面板。
 *
 * 文案全部来自后端（邀请码不正确 / 已用完 / 已过期），前端只负责排版与提交，
 * 保证两边说的话一致；登录成功后把令牌存到本地并回到上一页。
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { ApiError, api } from "@/lib/api/client";
import { saveToken } from "@/lib/session";

export function LoginPanel() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);

  async function submit() {
    const value = code.trim();
    if (!value) {
      setError("请输入邀请码");
      return;
    }
    setBusy(true);
    setError(null);
    setTraceId(null);
    try {
      const session = await api.login(value);
      saveToken(session.token, session.is_admin);
      router.replace("/");
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
        setTraceId(caught.traceId ?? null);
      } else {
        setError("服务暂时不可用，请稍后重试");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="p-7">
      <Eyebrow className="mb-2">邀请制</Eyebrow>
      <h1 className="text-[22px] font-semibold tracking-[-0.01em]">输入邀请码进入</h1>
      <p className="mt-2 max-w-[52ch] text-[14px] leading-relaxed text-muted">
        这是一个邀请制工具，需要一个可用的邀请码才能开始提取逐字稿。
        邀请码由管理员分发，如果你还没有，请联系管理员获取。
      </p>

      <label htmlFor="invite-code" className="mt-6 block text-[13px] text-muted">
        邀请码
      </label>
      <input
        id="invite-code"
        value={code}
        autoComplete="off"
        spellCheck={false}
        disabled={busy}
        placeholder="例如 ABCD-EFGH"
        onChange={(event) => {
          setCode(event.target.value);
          setError(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") void submit();
        }}
        className="mt-1.5 h-11 w-full rounded-control border border-line bg-canvas px-3 font-mono text-[15px] tracking-[0.04em] uppercase text-ink placeholder:tracking-normal placeholder:text-faint focus:border-line-strong"
      />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={() => void submit()} disabled={busy}>
          {busy ? "正在验证…" : "进入"}
        </Button>
      </div>

      {error ? (
        <div className="mt-4 border-l-2 border-danger pl-3">
          <p className="text-[14px] text-danger">{error}</p>
          {traceId ? (
            <p className="mt-1 font-mono text-[12px] text-faint">问题编号：{traceId}</p>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
