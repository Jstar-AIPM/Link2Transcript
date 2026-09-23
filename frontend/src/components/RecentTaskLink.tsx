"use client";

/** 首页的「继续查看上次任务」入口（本阶段不做历史列表，只记住最近一个）。 */
import Link from "next/link";
import { useEffect, useState } from "react";

import { readRecentTask, type RecentTask } from "@/lib/recent-task";

export function RecentTaskLink() {
  // localStorage 只能在浏览器里读，因此首屏不渲染，挂载后再显示，避免水合不一致
  const [recent, setRecent] = useState<RecentTask | null>(null);

  useEffect(() => {
    // localStorage 只能在浏览器里读：延后到挂载后再读，避免服务端渲染与水合不一致。
    const timer = window.setTimeout(() => setRecent(readRecentTask()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  if (!recent) {
    return <p className="text-[13px] text-faint">提交任务后，这里会记住最近一次任务，方便回来查看。</p>;
  }

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted">
        最近任务
      </span>
      <span className="flex items-center gap-2">
        <span className="font-mono text-[12px] text-faint">{recent.taskId.slice(0, 8)}</span>
        <Link
          href={`/tasks/${recent.taskId}`}
          className="text-[13px] text-ink underline underline-offset-2 hover:text-accent"
        >
          继续查看
        </Link>
      </span>
    </div>
  );
}
