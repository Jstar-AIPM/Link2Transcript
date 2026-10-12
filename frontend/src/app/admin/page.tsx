import type { Metadata } from "next";

import { AdminScreen } from "@/features/admin/AdminScreen";

export const metadata: Metadata = {
  title: "邀请码管理 ｜ 逐字稿提取器",
};

/** 只有管理员邀请码登录后才有权限（页面内校验角色，接口再校验一次）。 */
export default function AdminPage() {
  return <AdminScreen />;
}
