import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto w-full max-w-[var(--page-max-width)] px-4 py-16 sm:px-6">
      <h1 className="text-[28px] font-medium text-ink">页面不存在</h1>
      <p className="mt-2 text-[16px] leading-relaxed text-ink-soft">
        这个地址没有对应的页面。可能是链接抄错了，或者任务已经被清理。
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-button bg-ink-fill px-4 py-2.5 text-[15px] text-ink-inverse transition-opacity hover:opacity-90"
      >
        回到首页，发起新任务
      </Link>
    </main>
  );
}
