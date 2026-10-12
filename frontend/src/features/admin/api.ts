/** 邀请码管理接口（仅管理员；错误文案一律来自后端）。 */
import { api } from "@/lib/api/client";

import type { AdminInvite, AdminInviteList, CreateInviteInput } from "./types";

export function listInvites(): Promise<AdminInviteList> {
  return api.listInviteCodes();
}

export function createInvite(input: CreateInviteInput): Promise<AdminInvite> {
  return api.createInviteCode(input).then((response) => response.code);
}

export function revokeInvite(code: string): Promise<AdminInvite> {
  return api.revokeInviteCode(code).then((response) => response.code);
}
