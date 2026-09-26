import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "ghost" | "quiet";

const BASE =
  "inline-flex items-center justify-center gap-2 h-10 rounded-control text-sm font-semibold " +
  "whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-45";

const VARIANTS: Record<ButtonVariant, string> = {
  // 主按钮：苹果官网蓝实心（与链接/进度/激活态同一族）
  primary: "bg-accent-button text-white px-4 hover:bg-accent-button-hover",
  ghost: "border border-line-strong bg-transparent px-4 text-ink hover:bg-sunken",
  quiet: "px-2 text-muted hover:text-ink",
};

/** 供 <Link> 等非 button 元素复用同一套外观。 */
export function buttonClass(variant: ButtonVariant = "primary", className?: string): string {
  return cn(BASE, VARIANTS[variant], className);
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant };

export function Button({ variant = "primary", className, type = "button", ...rest }: Props) {
  return <button type={type} className={buttonClass(variant, className)} {...rest} />;
}
