import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { TaskDetail } from "@/features/task-detail/TaskDetail";

export default async function TaskPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;

  return (
    <>
      <TopBar />
      <PageContainer>
        <div className="py-10 max-[900px]:py-7">
          <TaskDetail taskId={taskId} />
        </div>
      </PageContainer>
    </>
  );
}
