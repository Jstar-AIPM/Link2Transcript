/** 邀请码管理相关类型（从接口 schema 派生）。 */
export type {
  InviteCodeSummary as AdminInvite,
  InviteCodeList as AdminInviteList,
} from "@/lib/api/schemas";

export type CreateInviteInput = { uses: number; days: number; note: string };
