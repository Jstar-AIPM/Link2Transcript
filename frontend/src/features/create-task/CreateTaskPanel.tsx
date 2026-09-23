"use client";

/**
 * 创建任务面板：本地文件上传 / B 站链接提交。
 *
 * 设计要点（沿用 03 工具的设计语言：近单色 + hairline + 单一强调色）：
 * - 提交前只做**零成本弱校验**（格式、大小、链接像不像），把关仍在后端；
 * - 上传用 XHR 拿真实进度，并且**可以取消**（选错大文件不用干等）；
 * - 文案：能被后端给出的（如不支持格式、超限、非法链接）优先用后端文案；
 *   提交前的即时提示写在这里，措辞与后端保持一致；
 * - 成功后跳到任务页（`/tasks/{id}`），把「等待」交给任务页去表达。
 */
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { FileDropZone, validateFile } from "@/components/FileDropZone";
import { ModeSwitch } from "@/components/ModeSwitch";
import { ServiceUnavailable } from "@/components/ServiceUnavailable";
import { Bar } from "@/components/ui/Bar";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { useServiceConfig } from "@/features/service-config/useServiceConfig";
import { ApiError, api } from "@/lib/api/client";
import { uploadFile, type UploadHandle } from "@/lib/api/upload";
import { looksLikeBilibiliLink } from "@/lib/constants";

type Mode = "file" | "url";

type SubmitState =
  | { kind: "idle" }
  | { kind: "uploading"; percent: number; processing: boolean }
  | { kind: "creating" }
  | { kind: "error"; message: string; code: string };

export function CreateTaskPanel() {
  const router = useRouter();
  const { state: configState, reload } = useServiceConfig();

  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [submit, setSubmit] = useState<SubmitState>({ kind: "idle" });
  const [handle, setHandle] = useState<UploadHandle | null>(null);

  const maxUploadMb = configState.kind === "ready" ? configState.config.max_upload_mb : null;
  const maxMediaMinutes = configState.kind === "ready" ? configState.config.max_media_minutes : null;
  // 线上环境关闭本地上传（平台请求体上限 16 MiB）；配置未知时先按可用处理，避免误隐藏
  const uploadEnabled =
    configState.kind === "ready" ? configState.config.enable_local_upload : true;
  /** 不支持上传时，界面与提交都退化为链接模式 */
  const effectiveMode: Mode = uploadEnabled ? mode : "url";
  const busy = submit.kind === "uploading" || submit.kind === "creating";

  const fail = useCallback((error: unknown) => {
    if (error instanceof ApiError) {
      if (error.code === "UPLOAD_CANCELLED") {
        setNotice("已取消上传。你可以重新选择文件后再提交。");
        setSubmit({ kind: "idle" });
        return;
      }
      setSubmit({ kind: "error", message: error.message, code: error.code });
      return;
    }
    setSubmit({ kind: "error", message: "提交失败，请稍后重试", code: "UNKNOWN" });
  }, []);

  const goToTask = useCallback(
    (taskId: string) => {
      router.push(`/tasks/${taskId}`);
    },
    [router],
  );

  async function submitFile() {
    if (!file) {
      setSubmit({ kind: "error", message: "请先选择文件", code: "MISSING_FILE" });
      return;
    }
    const invalid = validateFile(file, maxUploadMb);
    if (invalid) {
      setSubmit({ kind: "error", message: invalid.message, code: "INVALID_FILE" });
      return;
    }

    setNotice(null);
    setSubmit({ kind: "uploading", percent: 0, processing: false });
    const upload = uploadFile(file, {
      onProgress: (percent) => setSubmit({ kind: "uploading", percent, processing: false }),
      // 100% 之后服务端还要落盘与建任务，文案要跟着变，否则看起来卡住
      onProcessing: () => setSubmit({ kind: "uploading", percent: 100, processing: true }),
    });
    setHandle(upload);
    try {
      const created = await upload.promise;
      goToTask(created.task_id);
    } catch (error) {
      fail(error);
    } finally {
      setHandle(null);
    }
  }

  async function submitUrl() {
    const value = url.trim();
    if (!value) {
      setSubmit({ kind: "error", message: "请先粘贴 B 站视频链接", code: "MISSING_URL" });
      return;
    }
    if (!looksLikeBilibiliLink(value)) {
      setSubmit({
        kind: "error",
        message: "链接格式不正确，请粘贴完整的 B 站视频链接",
        code: "INVALID_SOURCE_URL",
      });
      return;
    }

    setNotice(null);
    setSubmit({ kind: "creating" });
    try {
      const created = await api.createTaskFromUrl(value);
      goToTask(created.task_id);
    } catch (error) {
      fail(error);
    }
  }

  function onSubmit() {
    if (busy) return;
    setNotice(null);
    setSubmit({ kind: "idle" });
    if (effectiveMode === "file") {
      void submitFile();
    } else {
      void submitUrl();
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {configState.kind === "failed" ? (
        <ServiceUnavailable message={configState.message} onRetry={reload} />
      ) : null}

      <Card className="p-7">
        <Eyebrow className="mb-3">新建任务</Eyebrow>

        {uploadEnabled ? (
          <ModeSwitch
            value={mode}
            onChange={(next) => {
              setMode(next);
              setSubmit({ kind: "idle" });
              setNotice(null);
            }}
            disabled={busy}
            options={[
              { value: "file", label: "上传本地文件" },
              { value: "url", label: "粘贴 B 站链接" },
            ]}
          />
        ) : (
          <p className="rounded-control bg-sunken px-3 py-2 text-[13px] leading-relaxed text-body">
            当前环境只支持粘贴 B 站视频链接（线上暂不支持上传本地文件）。
          </p>
        )}

        <div className="mt-5">
          {effectiveMode === "file" ? (
            <FileDropZone
              file={file}
              onSelect={(picked) => {
                setFile(picked);
                setSubmit({ kind: "idle" });
                setNotice(null);
              }}
              onClear={() => {
                setFile(null);
                setSubmit({ kind: "idle" });
              }}
              disabled={busy}
              maxSizeMb={maxUploadMb}
            />
          ) : (
            <div>
              <label htmlFor="url-input" className="text-[13px] text-muted">
                B 站视频链接
              </label>
              <input
                id="url-input"
                type="url"
                inputMode="url"
                autoComplete="off"
                placeholder="https://www.bilibili.com/video/BV..."
                value={url}
                disabled={busy}
                onChange={(event) => {
                  setUrl(event.target.value);
                  setSubmit({ kind: "idle" });
                }}
                className="mt-1.5 h-11 w-full rounded-control border border-line bg-canvas px-3 text-[15px] text-ink placeholder:text-faint focus:border-line-strong"
              />
              <p className="mt-2 text-[13px] leading-relaxed text-muted">
                支持单个视频链接。多 P 视频请粘贴某一个分集的链接。
                {maxMediaMinutes && maxMediaMinutes > 0
                  ? ` 单条内容时长上限 ${maxMediaMinutes} 分钟。`
                  : " 不限制内容时长。"}
              </p>
            </div>
          )}
        </div>

        {submit.kind === "uploading" ? (
          <div className="mt-5">
            <Bar
              percent={submit.percent}
              label="上传进度"
              valueText={`已上传 ${submit.percent}%`}
            />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
              <p className="font-mono text-[13px] text-muted">
                {submit.processing ? "正在创建任务…" : `正在上传 ${submit.percent}%`}
              </p>
              <button
                type="button"
                onClick={() => handle?.abort()}
                className="h-8 rounded-control border border-line-strong px-3 text-[13px] text-muted transition-colors hover:bg-sunken hover:text-ink"
              >
                取消上传
              </button>
            </div>
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button onClick={onSubmit} disabled={busy || configState.kind === "failed"}>
            开始提取
          </Button>
          {submit.kind === "creating" ? (
            <span className="text-[13.5px] text-muted">正在创建任务…</span>
          ) : null}
        </div>

        {notice ? (
          <p className="mt-4 rounded-control bg-sunken px-3 py-2 text-[13.5px] text-body">
            {notice}
          </p>
        ) : null}

        {submit.kind === "error" ? (
          <div className="mt-4 border-l-2 border-danger pl-3">
            <p className="text-[14px] text-danger">{submit.message}</p>
            {submit.code === "FILE_TOO_LARGE" || submit.code === "INVALID_FILE" ? (
              <p className="mt-1 text-[13px] text-muted">
                提示：也可以先截取需要转写的片段，再上传。
              </p>
            ) : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
}
