"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { PageContainer } from "@/components/layout/PageContainer";
import { cn } from "@/lib/utils";

/** 顶栏：滚动后出现 1px hairline（与 03 工具同构）。 */
export function TopBar({ children }: { children?: ReactNode }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-30 border-b border-transparent bg-canvas/88 backdrop-blur-md transition-colors",
        scrolled && "border-line",
      )}
    >
      <PageContainer>
        <div className="flex h-16 items-center gap-6">
          <Link
            href="/"
            className="flex items-center gap-2.5 font-semibold tracking-[-0.01em] whitespace-nowrap"
          >
            <span className="grid size-[22px] place-items-center rounded-[5px] bg-accent-button font-mono text-[11px] text-white">
              稿
            </span>
            逐字稿提取器
          </Link>
          <div className="flex-1" />
          <div className="flex items-center gap-2.5">{children}</div>
        </div>
      </PageContainer>
    </header>
  );
}
