"use client";

/**
 * 逐字稿阅读区：逐段追加渲染 + 自动跟随（可暂停）。
 *
 * 三条设计决定：
 * 1. **只追加，不整体重绘**：长内容有数千段，整体重绘会卡；这里每次只 append 新行；
 * 2. **自动跟随但不抢用户操作**：用户手动上滚时立刻暂停跟随并出现「回到最新」，
 *    滚回底部自动恢复；
 * 3. **不用平滑滚动动画**：转写每秒都在追加，平滑滚动会互相打断；
 *    同时天然满足「减少动效」偏好。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Segment } from "@/lib/api/schemas";

/** 距底部小于这个像素数就算「在底部」，避免 1px 误差导致跟随失效 */
const FOLLOW_THRESHOLD_PX = 24;

export function TranscriptViewer({
  segments,
  emptyHint,
  headerActions,
}: {
  segments: Segment[];
  emptyHint: string;
  /** 右侧操作区（如「复制全文」），由调用方决定什么时候显示 */
  headerActions?: React.ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const previousCountRef = useRef(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (segments.length > previousCountRef.current && following) {
      container.scrollTop = container.scrollHeight;
    }
    previousCountRef.current = segments.length;
  }, [segments.length, following]);

  const onScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const distance = container.scrollHeight - container.scrollTop - container.clientHeight;
    setFollowing(distance < FOLLOW_THRESHOLD_PX);
  }, []);

  const backToLatest = useCallback(() => {
    const container = containerRef.current;
    setFollowing(true);
    if (container) container.scrollTop = container.scrollHeight;
  }, []);

  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted">
          逐字稿{segments.length > 0 ? ` · ${segments.length} 段` : ""}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {headerActions}
          {!following ? (
            <button
              type="button"
              onClick={backToLatest}
              className="h-9 rounded-control border border-line-strong px-3 text-[13px] text-ink transition-colors hover:bg-sunken"
            >
              回到最新
            </button>
          ) : null}
        </div>
      </div>

      <div
        ref={containerRef}
        onScroll={onScroll}
        data-testid="transcript-scroll"
        className="mt-2.5 max-h-[460px] overflow-auto rounded-card border border-line bg-surface px-5 py-4"
      >
        {segments.length === 0 ? (
          <p className="text-[14px] text-muted">{emptyHint}</p>
        ) : (
          <ol className="space-y-1">
            {segments.map((segment) => (
              <li key={segment.index} className="grid grid-cols-[62px_1fr] gap-3.5">
                <span className={cn("timestamp mt-[3px] font-mono text-[12px] text-faint")}>
                  {formatTimestamp(segment.start)}
                </span>
                <span className="text-[16px] leading-[1.75] text-body">{segment.text}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
