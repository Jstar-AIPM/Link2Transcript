/**
 * 提交前的弱校验（真正的把关在后端）。
 *
 * 为什么前端还要做：如果完全交给后端，用户要先上传完 2 GB 才被告知
 * 「格式不支持」——那是很差的体验。这里只做**零成本的即时判断**，
 * 文案与后端保持一致，避免两边说法不一样。
 */
export const ALLOWED_EXTENSIONS = ["mp3", "m4a", "wav", "mp4", "mov"] as const;

export const ALLOWED_EXTENSIONS_LABEL = "MP3 / M4A / WAV / MP4 / MOV";

export function extensionOf(filename: string): string {
  const parts = filename.split(".");
  return parts.length > 1 ? (parts[parts.length - 1] ?? "").toLowerCase() : "";
}

export function isSupportedFilename(filename: string): boolean {
  return (ALLOWED_EXTENSIONS as readonly string[]).includes(extensionOf(filename));
}

/**
 * 从粘贴文本里提取第一个 http(s) 链接。
 *
 * 用户在 App 里点“复制链接”得到的往往是**一整段分享文案**（标题 + 链接 + 口令），
 * 因此输入框要允许整段粘贴，由我们从文本里找出真实链接。
 * 行为与后端的 `first_url_in_text` 保持一致（真正的把关仍在后端）。
 */
export function extractUrlFromText(value: string): string | null {
  const match = /https?:\/\/[^\s\u3000<>"']+/i.exec(value ?? "");
  if (!match) return null;
  // 链接贴在句末时常被标点粘上，去掉尾部标点
  return match[0].replace(/[。，、；：！？）】》」』…,.!?;:)\]}"']+$/, "");
}

/**
 * 当前支持平台的弱校验（只判断“像不像”，真白名单在后端）：
 * B 站子域 / b23.tv 短链；小红书 xiaohongshu.com / xhslink 短链。
 */
export function isSupportedLink(url: string): boolean {
  return /^https?:\/\/([\w-]+\.)*(bilibili\.com|b23\.tv|xiaohongshu\.com|xhslink\.com|xhslink\.cn)\//i.test(
    url.trim(),
  );
}

/** 大文件提示用：把字节说成人话（与后端一致用 MB / GB） */
export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / (1024 * 1024)))} MB`;
}
