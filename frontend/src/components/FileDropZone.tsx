"use client";

import { useRef, useState } from "react";

import { ALLOWED_EXTENSIONS_LABEL, formatFileSize, isSupportedFilename } from "@/lib/constants";
import { cn } from "@/lib/utils";

export type FileSelectionError = { message: string } | null;

/**
 * 文件选择区：点击选择 + 拖拽放入。
 *
 * 可访问性：真正可聚焦、可键盘操作的是 input[type=file]（视觉隐藏但保留），
 * label 负责点击区域，拖拽只是额外的便利入口 —— 不做「只能拖拽」的设计。
 */
export function FileDropZone({
  file,
  onSelect,
  onClear,
  disabled,
  maxSizeMb,
}: {
  file: File | null;
  onSelect: (file: File) => void;
  onClear: () => void;
  disabled?: boolean;
  maxSizeMb: number | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const accept = ".mp3,.m4a,.wav,.mp4,.mov";

  function handleFiles(files: FileList | null) {
    const picked = files?.[0];
    if (picked) onSelect(picked);
  }

  return (
    <div>
      <input
        ref={inputRef}
        id="file-input"
        type="file"
        accept={accept}
        className="sr-only"
        disabled={disabled}
        onChange={(event) => handleFiles(event.target.files)}
      />

      {file ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-control border border-line bg-canvas px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-[15px] font-medium">{file.name}</p>
            <p className="mt-0.5 font-mono text-[13px] text-muted">{formatFileSize(file.size)}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              onClear();
              if (inputRef.current) inputRef.current.value = "";
            }}
            disabled={disabled}
            className="h-9 rounded-control border border-line-strong px-3 text-[13.5px] text-ink transition-colors hover:bg-sunken disabled:opacity-45"
          >
            重新选择
          </button>
        </div>
      ) : (
        <label
          htmlFor="file-input"
          onDragOver={(event) => {
            event.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            if (!disabled) handleFiles(event.dataTransfer.files);
          }}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-card border border-dashed px-4 py-10 text-center transition-colors",
            dragging ? "border-accent bg-accent-wash" : "border-line-strong hover:bg-canvas",
            disabled && "cursor-not-allowed opacity-60",
          )}
        >
          <span className="text-[15px] font-medium">点击选择文件，或把文件拖到这里</span>
          <span className="mt-1.5 text-[13px] leading-relaxed text-muted">
            支持 {ALLOWED_EXTENSIONS_LABEL}
            {maxSizeMb ? `，单个文件不超过 ${maxSizeMb} MB` : ""}
          </span>
        </label>
      )}
    </div>
  );
}

/** 提交前校验：返回中文提示，null 表示通过。文案与后端保持一致。 */
export function validateFile(file: File, maxSizeMb: number | null): FileSelectionError {
  if (!isSupportedFilename(file.name)) {
    return { message: `当前不支持该文件格式，请改用 ${ALLOWED_EXTENSIONS_LABEL} 文件` };
  }
  if (maxSizeMb !== null && file.size > maxSizeMb * 1024 * 1024) {
    return { message: `文件不能超过 ${maxSizeMb} MB` };
  }
  if (file.size === 0) {
    return { message: "该文件是空文件，请确认后重新选择" };
  }
  return null;
}
