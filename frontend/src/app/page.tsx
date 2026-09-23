import { BackendCheck } from "@/components/BackendCheck";
import { apiInternals } from "@/lib/api/client";

export default function HomePage() {
  return (
    <main className="mx-auto w-full max-w-[var(--page-max-width)] px-4 py-10 sm:px-6 sm:py-14">
      <h1 className="text-[28px] font-medium leading-tight text-ink">逐字稿提取器</h1>
      <p className="mt-2 text-[16px] leading-relaxed text-ink-soft">
        上传音频或视频，或者粘贴 B 站链接，生成可阅读、可下载的中文逐字稿。
        长内容转写期间内容会持续出现，中途刷新或服务重启都不会白跑。
      </p>

      <div className="mt-6 space-y-4">
        <BackendCheck />
      </div>

      <footer className="mt-8 border-t border-hairline pt-4 text-[13px] leading-relaxed text-ink-muted">
        <p>
          当前进度：阶段 4 的第一步（4A）已完成工程接入 —— 设计变量、接口客户端（含运行时校验）、
          同源代理（{apiInternals.API_PREFIX}）。
        </p>
        <p className="mt-1">
          提交任务与实时逐字稿界面在 4B / 4C 实现；接口与后端保持不变。
        </p>
      </footer>
    </main>
  );
}
