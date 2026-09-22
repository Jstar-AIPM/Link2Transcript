from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from time import perf_counter

from backend.app.core.errors import AppError
from backend.app.core.filenames import sanitize_filename
from backend.app.core.messages import (
    file_too_long_message,
    format_duration,
    incomplete_audio_message,
)
from backend.app.schemas.task import (
    ExtractMethod,
    MediaType,
    ProcessingMethod,
    SourceType,
    SubtitleKind,
    TaskArtifacts,
    TaskStatus,
    TranscriptResult,
)
from backend.app.services.download_service import DownloadService
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)

# 实际音频短于声明时长的这个比例时，判定为不完整（防意外截断的通用安全网）。
MIN_AUDIO_COVERAGE_RATIO = 0.9

# “没有人声”类错误的用户文案：同一错误码在不同来源下说人话。
SPEECH_ERROR_MESSAGES: dict[str, dict[SourceType, str]] = {
    "SILENT_AUDIO": {
        SourceType.LOCAL_FILE: (
            "未在文件中检测到人声。请确认文件是否正确，或更换包含人声的文件后重试。"
        ),
        SourceType.PLATFORM_URL: "未在该视频中检测到人声。请确认视频是否包含人声内容。",
    },
    "EMPTY_TRANSCRIPT": {
        SourceType.LOCAL_FILE: "未能从文件中提取到语音内容。请确认文件是否正确，或更换文件后重试。",
        SourceType.PLATFORM_URL: "未能从该视频中提取到语音内容。请确认视频内容后重试。",
    },
}


def _rethrow_with_source_wording(exc: AppError, source_type: SourceType) -> None:
    """把服务层的通用错误换成针对当前来源的具体说法，然后重新抛出。"""
    message = SPEECH_ERROR_MESSAGES.get(exc.code, {}).get(source_type)
    if message:
        raise AppError(exc.code, message) from exc


class TaskProcessor:
    def __init__(
        self,
        *,
        task_service: TaskService,
        media_service: MediaService,
        transcription_service: TranscriptionService,
        export_service: ExportService,
        download_service: DownloadService,
        subtitle_service: SubtitleService,
        uploads_dir: Path,
        audio_dir: Path,
        downloads_dir: Path,
        max_media_seconds: float = 180 * 60,
        max_media_minutes: int = 180,
        max_workers: int = 1,
    ) -> None:
        self.task_service = task_service
        self.media_service = media_service
        self.transcription_service = transcription_service
        self.export_service = export_service
        self.download_service = download_service
        self.subtitle_service = subtitle_service
        self.uploads_dir = uploads_dir
        self.audio_dir = audio_dir
        self.downloads_dir = downloads_dir
        self.max_media_seconds = max_media_seconds
        self.max_media_minutes = max_media_minutes
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="transcript")

    def submit(self, task_id: str) -> None:
        self.executor.submit(self.process, task_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    # ------------------------------------------------------------------ 调度

    def process(self, task_id: str) -> None:
        task_started = perf_counter()
        try:
            record = self.task_service.get(task_id)
            if record.source_type == SourceType.PLATFORM_URL:
                self._process_platform(task_id, record, task_started)
            else:
                self._process_local(task_id, record, task_started)
            logger.info(
                "task_succeeded task_id=%s duration_seconds=%.3f",
                task_id,
                perf_counter() - task_started,
            )
        except AppError as exc:
            logger.warning(
                "task_failed task_id=%s code=%s duration_seconds=%.3f",
                task_id,
                exc.code,
                perf_counter() - task_started,
            )
            self.task_service.fail(
                task_id,
                code=exc.code,
                message=exc.message,
                internal_type=type(exc).__name__,
            )
        except Exception as exc:
            logger.exception(
                "task_failed_unexpected task_id=%s type=%s duration_seconds=%.3f",
                task_id,
                type(exc).__name__,
                perf_counter() - task_started,
            )
            self.task_service.fail(
                task_id,
                code="INTERNAL_PROCESSING_ERROR",
                message="处理失败，请重试。原始文件不会被修改",
                internal_type=type(exc).__name__,
            )

    # ------------------------------------------------------- 路径一：本地文件

    def _process_local(self, task_id: str, record, task_started: float) -> None:
        stage_started = perf_counter()
        record = self.task_service.transition(task_id, TaskStatus.VALIDATING)
        source = self.uploads_dir / task_id / record.stored_filename
        if not source.is_file():
            raise AppError("UPLOAD_NOT_FOUND", "上传文件不存在")
        media_info = self.media_service.inspect(source, record.media_type)
        self._ensure_duration_within_limit(media_info.duration_seconds)
        self.task_service.set_media_duration(task_id, media_info.duration_seconds)
        self._log_stage(task_id, TaskStatus.VALIDATING, stage_started)

        audio_path = source
        if record.media_type == MediaType.VIDEO:
            stage_started = perf_counter()
            self.task_service.transition(task_id, TaskStatus.EXTRACTING_AUDIO)
            audio_path = self.audio_dir / task_id / "audio.wav"
            self.media_service.extract_audio(source, audio_path)
            self._log_stage(task_id, TaskStatus.EXTRACTING_AUDIO, stage_started)

        self._transcribe_and_export(
            task_id,
            audio_path=audio_path,
            fallback_duration=media_info.duration_seconds,
            processing_method=ProcessingMethod.SPEECH_TO_TEXT_LOCAL,
            source_type=record.source_type,
            task_started=task_started,
        )

    # --------------------------------------------------------- 路径二：链接

    def _process_platform(self, task_id: str, record, task_started: float) -> None:
        url = record.resolved_url or record.source_url
        if not url:
            raise AppError("INVALID_SOURCE_URL", "链接格式不正确，请粘贴完整的 B 站视频链接")

        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.CHECKING_SUBTITLE)
        meta = self.download_service.probe(url)
        self.task_service.set_media_duration(task_id, meta.duration_seconds)
        if meta.title:
            self.task_service.set_original_filename(
                task_id,
                sanitize_filename(meta.title, fallback=meta.video_id or task_id),
            )
        self._log_stage(task_id, TaskStatus.CHECKING_SUBTITLE, stage_started)

        selection = self.subtitle_service.select(meta.subtitles, meta.automatic_captions)
        if selection is not None:
            self._finish_with_subtitle(task_id, selection, meta, task_started)
            return

        # 无可用字幕：只下载音频轨，然后复用阶段 1 已验证的转写链路。
        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.DOWNLOADING_AUDIO)
        downloaded = self.download_service.download_audio(
            url, self.downloads_dir / task_id, task_id
        )
        self.task_service.set_downloaded_bytes(task_id, downloaded.size_bytes)
        wav_path = self.audio_dir / task_id / "audio.wav"
        self.media_service.extract_audio(downloaded.path, wav_path)
        self._ensure_audio_complete(wav_path, meta.duration_seconds)
        self._log_stage(task_id, TaskStatus.DOWNLOADING_AUDIO, stage_started)

        self._transcribe_and_export(
            task_id,
            audio_path=wav_path,
            fallback_duration=meta.duration_seconds,
            processing_method=ProcessingMethod.SPEECH_TO_TEXT_REMOTE,
            source_type=record.source_type,
            task_started=task_started,
        )

    def _finish_with_subtitle(self, task_id: str, selection, meta, task_started: float) -> None:
        processing_method = (
            ProcessingMethod.SUBTITLE_BILIBILI_CC
            if selection.kind == SubtitleKind.CC
            else ProcessingMethod.SUBTITLE_BILIBILI_AI
        )
        record = self.task_service.set_extraction(
            task_id,
            processing_method=processing_method,
            extract_method=ExtractMethod.SUBTITLE,
            subtitle_kind=selection.kind,
        )
        result = TranscriptResult(
            task_id=task_id,
            source_type=record.source_type,
            platform=record.platform,
            source_url=record.source_url,
            original_filename=record.original_filename,
            media_type=record.media_type,
            processing_method=processing_method,
            subtitle_kind=selection.kind,
            language=selection.language,
            duration_seconds=meta.duration_seconds,
            text=selection.text,
            segments=selection.segments,
            generated_at=datetime.now().astimezone(),
        )
        self._export_and_succeed(task_id, result, task_started)

    # --------------------------------------------------------------- 共用尾部

    def _transcribe_and_export(
        self,
        task_id: str,
        *,
        audio_path: Path,
        fallback_duration: float | None,
        processing_method: ProcessingMethod,
        source_type: SourceType,
        task_started: float,
    ) -> None:
        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.TRANSCRIBING)
        try:
            transcription = self.transcription_service.transcribe(audio_path)
        except AppError as exc:
            _rethrow_with_source_wording(exc, source_type)
            raise
        self._log_stage(task_id, TaskStatus.TRANSCRIBING, stage_started)

        record = self.task_service.set_extraction(
            task_id,
            processing_method=processing_method,
            extract_method=ExtractMethod.SPEECH_TO_TEXT,
        )
        result = TranscriptResult(
            task_id=task_id,
            source_type=record.source_type,
            platform=record.platform,
            source_url=record.source_url,
            original_filename=record.original_filename,
            media_type=record.media_type,
            processing_method=processing_method,
            language=transcription.language,
            duration_seconds=transcription.duration_seconds or fallback_duration,
            text=transcription.text,
            segments=transcription.segments,
            generated_at=datetime.now().astimezone(),
        )
        self._export_and_succeed(task_id, result, task_started)

    def _export_and_succeed(self, task_id: str, result: TranscriptResult, task_started: float) -> None:
        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.EXPORTING)
        markdown_path, txt_path, result_path = self.export_service.export(result)
        artifacts = TaskArtifacts(
            markdown=str(markdown_path),
            txt=str(txt_path),
            result=str(result_path),
        )
        self.task_service.transition(task_id, TaskStatus.SUCCEEDED, artifacts=artifacts)
        self._log_stage(task_id, TaskStatus.EXPORTING, stage_started)

    def _ensure_duration_within_limit(self, duration_seconds: float | None) -> None:
        if duration_seconds is None or duration_seconds <= self.max_media_seconds:
            return
        raise AppError(
            "VIDEO_TOO_LONG",
            file_too_long_message(duration_seconds, self.max_media_minutes),
        )

    def _ensure_audio_complete(self, audio_path: Path, declared_seconds: float | None) -> None:
        """通用安全网：交叉校验实际音频时长与声明时长。

        防止任何形式的“静默截断”（会员预览片段、部分下载、平台异常）
        被当成完整转写向外呈现。
        """
        if not declared_seconds or declared_seconds <= 0:
            return
        actual = self.media_service.inspect(audio_path, MediaType.AUDIO).duration_seconds
        if actual is None:
            return
        if actual < declared_seconds * MIN_AUDIO_COVERAGE_RATIO:
            raise AppError(
                "DOWNLOAD_INCOMPLETE",
                incomplete_audio_message(actual, declared_seconds),
            )

    @staticmethod
    def _log_stage(task_id: str, stage: TaskStatus, started_at: float) -> None:
        logger.info(
            "task_stage_completed task_id=%s stage=%s duration_seconds=%.3f",
            task_id,
            stage.value,
            perf_counter() - started_at,
        )
