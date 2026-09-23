import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { RecentTaskLink } from "@/components/RecentTaskLink";
import { CreateTaskPanel } from "@/features/create-task/CreateTaskPanel";

const STEPS: Array<[string, string, string]> = [
  ["01", "提交内容", "上传音频/视频，或粘贴 B 站视频链接"],
  ["02", "看着它生成", "逐段实时出现，进度可见，可随时取消"],
  ["03", "阅读与导出", "在线阅读、复制全文，导出 Markdown / TXT"],
];

const LIMITS: Array<[string, string]> = [
  ["支持格式", "MP3 / M4A / WAV / MP4 / MOV"],
  ["内容时长", "单条上限 6 小时，4–5 小时播客正常支持"],
  ["视频链接", "目前支持 B 站单个视频（多 P 请贴分集链接）"],
  ["不支持", "画面烧录字幕（硬字幕）、大会员专享视频"],
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
              上传本地文件，或粘贴 B 站链接。长内容会边转写边出现在页面上，
              中途刷新或服务重启都不会白跑，完成后可直接阅读、复制或导出。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[13px] text-muted">
              <span>提交</span>
              <span className="text-faint">→</span>
              <span>解析 / 下载</span>
              <span className="text-faint">→</span>
              <span>转写</span>
              <span className="text-faint">→</span>
              <span>逐段呈现</span>
              <span className="text-faint">→</span>
              <span>导出</span>
            </div>

            <div className="mt-14 grid grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-7 max-[900px]:grid-cols-1 max-[900px]:gap-4 max-[900px]:mt-9">
              <CreateTaskPanel />

              <div className="flex flex-col gap-5">
                <Card className="px-6 pt-5 pb-2">
                  {STEPS.map(([number, title, detail]) => (
                    <div
                      key={number}
                      className="grid grid-cols-[34px_1fr] gap-3 border-b border-line py-3 last:border-b-0"
                    >
                      <span className="pt-0.5 font-mono text-xs text-muted">{number}</span>
                      <div>
                        <p className="text-[14.5px] font-semibold">{title}</p>
                        <p className="text-[13.5px] text-muted">{detail}</p>
                      </div>
                    </div>
                  ))}
                </Card>

                <Card className="px-6 pt-5 pb-5">
                  <Eyebrow className="mb-2">能力与限制</Eyebrow>
                  <dl className="space-y-2.5">
                    {LIMITS.map(([label, value]) => (
                      <div key={label} className="grid grid-cols-[72px_1fr] gap-3">
                        <dt className="text-[13px] text-muted">{label}</dt>
                        <dd className="text-[13.5px] text-body">{value}</dd>
                      </div>
                    ))}
                  </dl>
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
