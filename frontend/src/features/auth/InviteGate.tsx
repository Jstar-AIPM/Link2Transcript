"use client";

/**
 * 邀请码门禁（整页）。
 *
 * 未通过校验时**不渲染任何业务页面**，避免"看起来能用、其实会被接口拒"。
 * 本地开发（`require_auth=false`）直接放行，保持既有调试体验不变。
 */
import { useState, type ReactNode } from "react";

import { PageContainer } from "@/components/layout/PageContainer";
import { ServiceUnavailable } from "@/components/ServiceUnavailable";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Input } from "@/components/ui/Input";
import { useServiceConfig } from "@/features/service-config/useServiceConfig";

import { useInvite } from "./useInvite";

const SAMPLE_HINT = "例如 ABCD-EFGH";

function BrandMark() {
  return (
    <div className="flex items-center gap-2.5 font-semibold tracking-[-0.01em]">
      <span className="grid size-[22px] place-items-center rounded-[5px] bg-accent-button font-mono text-[11px] text-white">
        稿
      </span>
      逐字稿提取器
    </div>
  );
}

/** 启动中的中性画面：避免"先闪一下门禁、再进工具"。 */
function Splash({ message }: { message: string }) {
  return (
    <div className="flex min-h-dvh items-center justify-center">
      <p className="text-[13.5px] text-muted">{message}</p>
    </div>
  );
}

export function InviteGate({ children }: { children: ReactNode }) {
  const { state: configState, reload } = useServiceConfig();
  const { phase, error, submitting, submit } = useInvite();
  const [code, setCode] = useState("");

  if (configState.kind === "loading") return <Splash message="正在读取服务配置…" />;

  if (configState.kind === "failed") {
    return (
      <div className="dot-grid min-h-dvh">
        <PageContainer className="flex min-h-dvh items-center py-16">
          <div className="mx-auto w-full max-w-[560px]">
            <BrandMark />
            <div className="mt-10">
              <ServiceUnavailable message={configState.message} onRetry={reload} />
            </div>
          </div>
        </PageContainer>
      </div>
    );
  }

  // 本地开发不强制登录（配置里 require_auth=false）：直接放行
  if (!configState.config.require_auth) return <>{children}</>;

  if (phase === "loading") return <Splash message="正在读取登录状态…" />;
  if (phase === "unlocked") return <>{children}</>;

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!code.trim()) return;
    const ok = await submit(code);
    if (ok) setCode("");
  }

  return (
    <div className="dot-grid min-h-dvh">
      <PageContainer className="flex min-h-dvh items-center py-16">
        <div className="mx-auto w-full max-w-[560px]">
          <BrandMark />

          <div className="mt-12">
            <Eyebrow className="mb-3">邀请制 · 内测中</Eyebrow>
            <h1 className="display text-[clamp(28px,4vw,40px)]">输入邀请码开始使用</h1>
            <p className="mt-4 max-w-[48ch] text-[15px] text-muted">
              粘贴 B 站或小红书视频链接，也可以上传本地音视频，把内容变成可阅读、可下载的逐字稿 ——
              支持字幕提取与语音转写，全程可见进度。
            </p>
          </div>

          <Card className="mt-9 p-6">
            <form onSubmit={onSubmit} noValidate>
              <label
                htmlFor="invite-code"
                className="font-mono text-[11px] tracking-[0.08em] text-muted uppercase"
              >
                邀请码
              </label>

              <div className="mt-2.5 flex items-start gap-2.5 max-[520px]:flex-col">
                <Input
                  id="invite-code"
                  name="code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder={SAMPLE_HINT}
                  autoComplete="off"
                  autoCapitalize="characters"
                  spellCheck={false}
                  aria-describedby="invite-hint"
                  aria-invalid={error ? true : undefined}
                  className="font-mono tracking-[0.02em] uppercase"
                  autoFocus /* 门禁页只有这一个输入框，聚焦它就是正确的默认行为 */
                />
                <Button
                  type="submit"
                  disabled={submitting || code.trim() === ""}
                  className="max-[520px]:w-full"
                >
                  {submitting ? "校验中…" : "进入"}
                </Button>
              </div>

              {error ? (
                <p role="alert" data-testid="invite-error" className="mt-3 text-[13px] text-danger">
                  {error}
                </p>
              ) : null}

              <p id="invite-hint" className="mt-3 text-[12.5px] text-muted">
                邀请码由管理员提供，默认 20 次体验、30 天内有效。同一台设备验证一次后会记住登录状态。
              </p>
            </form>
          </Card>

          <p className="mt-6 text-[12.5px] text-faint">
            邀请码输错、过期或次数用完时，这里会给出具体原因，不会只显示一句「失败」。
          </p>
        </div>
      </PageContainer>
    </div>
  );
}
