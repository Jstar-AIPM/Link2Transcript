import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { RecentTaskLink } from "@/components/RecentTaskLink";
import { CreateTaskPanel } from "@/features/create-task/CreateTaskPanel";
import { HomeGate } from "@/features/auth/HomeGate";

const STEPS: Array<[string, string, string]> = [
  ["01", "提交内容", "粘贴 B 站 / 小红书链接，也可以上传本地音视频"],
  ["02", "等待生成", "逐段实时出现，进度可见，随时可以取消"],
  ["03", "阅读与导出", "在线阅读、复制全文，导出 Markdown / TXT"],
];

/** 只展示用户真正需要的信息；技术实现细节（格式清单、失败边界）不在首页展开。 */
const SUPPORT: Array<[string, string]> = [
  ["支持平台", "B 站、小红书"],
  ["单条时长", "最长 2 小时"],
  ["导出格式", "Markdown / TXT"],
];

export default function HomePage() {
  return (
    <>
      <TopBar />

      <section className="dot-grid border-b border-line">
        <PageContainer>
          <div className="pt-20 pb-16 max-[900px]:pt-12 max-[900px]:pb-10">
            <h1 className="display max-w-[26ch] text-[clamp(30px,4.4vw,52px)]">
              把音视频里的内容，
              <br className="max-[760px]:hidden" />
              变成
              <span className="bg-[linear-gradient(transparent_58%,var(--color-accent-mark)_58%)] px-[0.08em]">
                可读可下载
              </span>
              的逐字稿。
            </h1>
            <p className="mt-5 max-w-[56ch] text-[18px] text-muted">
              粘贴 B 站或小红书视频链接，也可以上传本地音视频。从提交到拿到逐字稿全程可见，
              中途刷新也不会丢进度，完成后可随时阅读、复制或下载。
            </p>
            <p className="mt-3 text-[15px] text-muted">
              有字幕的视频直接提取，没有字幕也能自动转写。
            </p>

            <div className="mt-14 grid grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-7 max-[900px]:grid-cols-1 max-[900px]:gap-4 max-[900px]:mt-9">
              <HomeGate>
                <CreateTaskPanel />
              </HomeGate>

              <div className="flex flex-col gap-5">
                <Card className="px-6 pt-5 pb-2">
                  <Eyebrow className="mb-1">怎么用</Eyebrow>
                  {STEPS.map(([number, title, detail]) => (
                    <div
                      key={number}
                      className="grid grid-cols-[34px_1fr] gap-3 border-b border-line py-3 last:border-b-0"
                    >
                      <span className="pt-0.5 font-mono text-xs text-accent">{number}</span>
                      <div>
                        <p className="text-[14.5px] font-semibold">{title}</p>
                        <p className="text-[13.5px] text-muted">{detail}</p>
                      </div>
                    </div>
                  ))}
                </Card>

                <Card className="px-6 pt-5 pb-5">
                  <Eyebrow className="mb-2">支持范围</Eyebrow>
                  <dl className="space-y-2.5">
                    {SUPPORT.map(([label, value]) => (
                      <div key={label} className="grid grid-cols-[72px_1fr] gap-3">
                        <dt className="text-[13px] text-muted">{label}</dt>
                        <dd className="text-[13.5px] text-body">{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="mt-3 text-[13px] text-faint">抖音正在接入中。</p>
                  <div className="mt-4 border-t border-line pt-3">
                    <RecentTaskLink />
                  </div>
                </Card>
              </div>
            </div>
          </div>
        </PageContainer>
      </section>
    </>
  );
}
