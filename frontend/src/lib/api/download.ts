/**
 * 带鉴权的下载（阶段 6B）。
 *
 * 为什么不能直接用 `<a href>`：任务接口需要会话令牌，把令牌放进 URL 会留在
 * 浏览器历史、访问日志与 Referer 里。这里改为带 header 请求 → 取 blob → 本地保存。
 */
import { readToken } from "@/lib/session";

import { ApiError } from "./client";

export async function downloadArtifact(url: string, filename: string): Promise<void> {
  const token = readToken();
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new ApiError("DOWNLOAD_FAILED", "下载失败，请稍后重试", response.status);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
