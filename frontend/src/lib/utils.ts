/** 极简 className 拼接（不引入 clsx），与 03 工具一致。 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
