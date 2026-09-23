import { TaskDetail } from "@/features/task-detail/TaskDetail";

export default async function TaskPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;

  return (
    <main className="mx-auto w-full max-w-[var(--page-max-width)] px-4 py-10 sm:px-6 sm:py-14">
      <header className="mb-5">
        <h1 className="text-[28px] font-medium leading-tight text-ink">逐字稿提取器</h1>
        <p className="mt-1 text-[13px] text-ink-muted">任务 {taskId}</p>
      </header>
      <TaskDetail taskId={taskId} />
    </main>
  );
}
