"use client";

/**
 * 邀请码管理（仅管理员）。
 *
 * 一个邀请码就是一份体验名额：被邀请者用码登录，次数用完或过期后自动失效。
 * 生成后在列表里复制发出去即可，不需要改服务端配置、也不需要重启。
 *
 * 结构与交互对齐 03《AI 用户洞察分析器》的邀请码管理页；配色沿用本项目的苹果蓝体系。
 */
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button, buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Input } from "@/components/ui/Input";
import { getAuthSnapshot, getServerAuthSnapshot, subscribeAuth } from "@/features/auth/storage";
import { ApiError } from "@/lib/api/client";

import { createInvite, listInvites, revokeInvite } from "./api";

import type { AdminInvite } from "./types";

const noopSubscribe = () => () => {};

function statusOf(invite: AdminInvite): { label: string; tone: BadgeTone } {
  if (invite.revoked_at) return { label: "已停用", tone: "neutral" };
  if (invite.expires_at && new Date(invite.expires_at) < new Date())
    return { label: "已过期", tone: "neutral" };
  if (invite.remaining !== null && invite.remaining <= 0)
    return { label: "次数用完", tone: "neutral" };
  return { label: "可用", tone: "success" };
}

function quotaText(invite: AdminInvite): string {
  if (invite.max_uses === 0) return `不限次数（已用 ${invite.uses}）`;
  return `${invite.uses} / ${invite.max_uses}`;
}

function expiryText(invite: AdminInvite): string {
  if (!invite.expires_at) return "不过期";
  return invite.expires_at.slice(0, 10);
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="quiet"
      className="h-8 text-[12.5px]"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "已复制" : "复制"}
    </Button>
  );
}

/** 权限不足（普通邀请码用户误入 /admin）。 */
function NotAdmin() {
  return (
    <>
      <TopBar />
      <PageContainer>
        <div className="flex flex-col items-start gap-4 py-24">
          <Eyebrow>权限不足</Eyebrow>
          <h1 className="display text-[30px]">邀请码管理只有管理员能用</h1>
          <p className="max-w-[52ch] text-muted">
            当前登录的邀请码不是管理员。只有管理员码才拥有生成与停用邀请码的权限。
          </p>
          <Link href="/" className={buttonClass("primary")}>
            回到工具
          </Link>
        </div>
      </PageContainer>
    </>
  );
}

export function AdminScreen() {
  const { token, status } = useSyncExternalStore(
    subscribeAuth,
    getAuthSnapshot,
    getServerAuthSnapshot,
  );
  const hydrated = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );

  const [invites, setInvites] = useState<AdminInvite[]>([]);
  const [defaults, setDefaults] = useState({ uses: 20, days: 30 });
  const [uses, setUses] = useState("20");
  const [days, setDays] = useState("30");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fresh, setFresh] = useState<AdminInvite | null>(null);
  const initialized = useRef(false);

  const isAdmin = status?.is_admin === true;

  const refresh = useCallback(async () => {
    if (!token || !isAdmin) return;
    try {
      const data = await listInvites();
      setInvites(data.codes);
      setDefaults(data.defaults);
      // 只在首次加载时用服务端默认值填表，避免刷新列表时覆盖用户正在输入的值
      if (!initialized.current) {
        initialized.current = true;
        setUses(String(data.defaults.uses));
        setDays(String(data.defaults.days));
      }
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "读取邀请码失败，请稍后重试");
    }
  }, [token, isAdmin]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      if (!token || !isAdmin) return;
      await Promise.resolve();
      if (alive) await refresh();
    })();
    return () => {
      alive = false;
    };
  }, [token, isAdmin, refresh]);

  async function onCreate() {
    if (!token) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await createInvite({
        uses: Number(uses) || 0,
        days: Number(days) || 0,
        note: note.trim(),
      });
      setFresh(created);
      setNote("");
      setNotice(`已生成新邀请码：${created.code}`);
      await refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "生成失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke(code: string) {
    if (!token) return;
    setError(null);
    try {
      await revokeInvite(code);
      await refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "停用失败，请稍后重试");
    }
  }

  if (!hydrated) {
    return (
      <>
        <TopBar />
        <PageContainer>
          <p className="py-24 text-muted">正在读取登录状态…</p>
        </PageContainer>
      </>
    );
  }

  if (!isAdmin) return <NotAdmin />;

  return (
    <>
      <TopBar>
        <Link href="/" className={buttonClass("quiet", "h-8 text-[12.5px]")}>
          回到工具
        </Link>
      </TopBar>

      <PageContainer>
        <div className="pt-10 pb-3">
          <Eyebrow className="mb-2.5">管理员</Eyebrow>
          <h1 className="display text-[clamp(24px,2.6vw,32px)]">邀请码管理</h1>
          <p className="mt-2.5 max-w-[68ch] text-[14.5px] text-muted">
            一个邀请码就是一份体验名额：被邀请者用码登录，次数用完或过期后自动失效。
            生成后在下面复制发出去即可，不需要改服务端配置、也不需要重启。
          </p>
        </div>

        {/* 生成 */}
        <Card className="mt-5">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[11px] tracking-[0.08em] text-muted uppercase">次数</span>
              <Input
                value={uses}
                onChange={(event) => setUses(event.target.value.replace(/[^\d]/g, ""))}
                className="w-[92px] font-mono"
                inputMode="numeric"
                aria-label="体验次数（0 表示不限）"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[11px] tracking-[0.08em] text-muted uppercase">有效天数</span>
              <Input
                value={days}
                onChange={(event) => setDays(event.target.value.replace(/[^\d]/g, ""))}
                className="w-[92px] font-mono"
                inputMode="numeric"
                aria-label="有效天数（0 表示不过期）"
              />
            </label>
            <label className="flex min-w-[180px] flex-1 flex-col gap-1.5">
              <span className="font-mono text-[11px] tracking-[0.08em] text-muted uppercase">
                备注（给谁用，可留空）
              </span>
              <Input
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="例如：面试-张三"
                maxLength={80}
                aria-label="备注"
              />
            </label>
            <Button onClick={() => void onCreate()} disabled={busy}>
              {busy ? "生成中…" : "生成邀请码"}
            </Button>
          </div>

          <p className="mt-3 text-[12.5px] text-muted">
            次数填 <span className="font-mono">0</span> 表示不限次数，天数填{" "}
            <span className="font-mono">0</span> 表示不过期；默认 {defaults.uses} 次 · {defaults.days} 天。
          </p>

          {notice && fresh ? (
            <div
              data-testid="admin-notice"
              className="mt-4 flex flex-wrap items-center gap-3 rounded-control border border-line-strong bg-canvas px-4 py-3"
            >
              <span className="text-[12.5px] text-muted">刚生成的邀请码</span>
              <span className="font-mono text-[17px] tracking-[0.02em]">{fresh.code}</span>
              <CopyButton value={fresh.code} />
            </div>
          ) : null}

          {error ? (
            <p role="alert" data-testid="admin-error" className="mt-3 text-[13px] text-danger">
              {error}
            </p>
          ) : null}
        </Card>

        {/* 列表 */}
        <div className="mt-8 border-t border-line">
          <div className="grid grid-cols-[minmax(0,1fr)_84px_132px_104px_76px_110px] items-center gap-3 border-b border-line py-2.5 max-[900px]:hidden">
            {["邀请码", "来源", "用量", "到期", "状态", "操作"].map((label) => (
              <span key={label} className="font-mono text-[11px] tracking-[0.08em] text-muted uppercase">
                {label}
              </span>
            ))}
          </div>

          {invites.map((invite) => {
            const state = statusOf(invite);
            return (
              <div
                key={invite.code}
                className="grid grid-cols-[minmax(0,1fr)_84px_132px_104px_76px_110px] items-center gap-3 border-b border-line py-3 max-[900px]:grid-cols-1 max-[900px]:gap-1.5"
              >
                <div className="flex min-w-0 items-center gap-1">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-[13.5px]" title={invite.code}>
                      {invite.code}
                    </p>
                    {invite.note ? (
                      <p className="truncate text-[12px] text-muted">{invite.note}</p>
                    ) : null}
                  </div>
                  <CopyButton value={invite.code} />
                </div>
                <span className="font-mono text-[12px] text-muted">
                  {invite.source === "env" ? "环境变量" : "生成"}
                </span>
                <span className="font-mono text-[12px] whitespace-nowrap">{quotaText(invite)}</span>
                <span className="font-mono text-[12px] text-muted">{expiryText(invite)}</span>
                <Badge tone={state.tone} className="justify-self-start">
                  {state.label}
                </Badge>
                <div className="flex items-center gap-1">
                  {invite.source === "generated" && !invite.revoked_at ? (
                    <Button
                      variant="quiet"
                      className="h-8 text-[12.5px]"
                      onClick={() => void onRevoke(invite.code)}
                    >
                      停用
                    </Button>
                  ) : (
                    <span className="text-[12px] text-faint">—</span>
                  )}
                </div>
              </div>
            );
          })}

          {invites.length === 0 ? (
            <p className="py-6 text-[13.5px] text-muted">还没有任何邀请码。</p>
          ) : null}
        </div>

        <p className="mt-4 pb-24 text-[12.5px] text-muted">
          环境变量里预置的邀请码（含你的管理员码）不能在这里停用 —— 那需要在服务端配置里删除。
        </p>
      </PageContainer>
    </>
  );
}
