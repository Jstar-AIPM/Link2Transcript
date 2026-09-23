import Link from "next/link";

import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { buttonClass } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";

export default function NotFound() {
  return (
    <>
      <TopBar />
      <PageContainer>
        <div className="py-16">
          <Card className="p-7">
            <Eyebrow className="mb-2">404</Eyebrow>
            <h1 className="display text-[32px]">页面不存在</h1>
            <p className="mt-3 max-w-[52ch] text-[15px] leading-relaxed text-muted">
              这个地址没有对应的页面。可能是链接抄错了，或者任务已经被清理。
            </p>
            <Link href="/" className={buttonClass("primary", "mt-6")}>
              回到首页，发起新任务
            </Link>
          </Card>
        </div>
      </PageContainer>
    </>
  );
}
