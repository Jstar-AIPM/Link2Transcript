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

  if (!recent) return null;

  return (
    <p className="text-[13px] text-ink-muted">
      上次的任务还在：
      <Link
        href={`/tasks/${recent.taskId}`}
        className="ml-1 underline underline-offset-2 hover:text-ink"
      >
        继续查看
      </Link>
    </p>
  );
}
