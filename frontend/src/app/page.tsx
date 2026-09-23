import { CreateTaskPanel } from "@/features/create-task/CreateTaskPanel";

export default function HomePage() {
  return (
    <main className="mx-auto w-full max-w-[var(--page-max-width)] px-4 py-10 sm:px-6 sm:py-14">
      <header>
        <h1 className="text-[28px] font-medium leading-tight text-ink">逐字稿提取器</h1>
        <p className="mt-2 text-[16px] leading-relaxed text-ink-soft">
          上传音频或视频，或者粘贴 B 站链接，生成可阅读、可下载的中文逐字稿。
        </p>
        <p className="mt-1 text-[14px] leading-relaxed text-ink-muted">
          长内容转写期间内容会边转边出现；中途刷新页面或服务重启都不会白跑。
        </p>
      </header>

      <div className="mt-6">
        <CreateTaskPanel />
      </div>

      <footer className="mt-8 border-t border-hairline pt-4 text-[13px] leading-relaxed text-ink-muted">
        <p>
          视频链接目前支持 B 站；抖音、小红书、YouTube 在后续阶段评估。
          画面内烧录的字幕（硬字幕）与大会员专享视频当前不支持。
        </p>
      </footer>
    </main>
  );
}
