/**
 * 展示层格式化。规则（设计基调 §2.2）：
 * - 所有数字使用等宽数字（配合 CSS 的 .tnum），避免进度跳动；
 * - 面向用户的时长说法与后端保持一致（「1小时05分20秒」/「42分18秒」）。
 */

/** 秒 → 「1小时05分20秒」/「42分18秒」/「18秒」 */
export function formatDuration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  if (hours > 0) return `${hours}小时${pad(minutes)}分${pad(secs)}秒`;
  return `${minutes}分${pad(secs)}秒`;
}

/** 片段起始秒 → 「00:42:18」（逐字稿左侧时间戳） */
export function formatTimestamp(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

/** 进度百分比：后端已经算好，这里只做展示与夹紧，避免出现 -1% 或 101% */
export function formatPercent(value: number | null | undefined): string {
  const clamped = Math.min(100, Math.max(0, value ?? 0));
  return `${Number.isInteger(clamped) ? clamped : clamped.toFixed(1)}%`;
}

/** 文件名里的扩展名（用于「上传的是什么」这类说明） */
export function fileExtension(filename: string): string {
  const parts = filename.split(".");
  return parts.length > 1 ? parts[parts.length - 1].toUpperCase() : "";
}
