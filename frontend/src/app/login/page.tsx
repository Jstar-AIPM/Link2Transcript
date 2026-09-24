import { PageContainer } from "@/components/layout/PageContainer";
import { TopBar } from "@/components/layout/TopBar";
import { LoginPanel } from "@/features/auth/LoginPanel";

export default function LoginPage() {
  return (
    <>
      <TopBar />
      <PageContainer>
        <div className="py-14 max-[900px]:py-8">
          <LoginPanel />
        </div>
      </PageContainer>
    </>
  );
}
