from __future__ import annotations

import logging
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    MediaType,
    TaskArtifacts,
    TaskStatus,
    TranscriptResult,
)
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)


class TaskProcessor:
    def __init__(
        self,
        *,
        task_service: TaskService,
        media_service: MediaService,
        transcription_service: TranscriptionService,
        export_service: ExportService,
        uploads_dir: Path,
        audio_dir: Path,
        max_workers: int = 1,
    ) -> None:
        self.task_service = task_service
        self.media_service = media_service
        self.transcription_service = transcription_service
        self.export_service = export_service
        self.uploads_dir = uploads_dir
        self.audio_dir = audio_dir
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="transcript")

    def submit(self, task_id: str) -> None:
        self.executor.submit(self.process, task_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    def process(self, task_id: str) -> None:
        task_started = perf_counter()
        try:
            stage_started = perf_counter()
            record = self.task_service.transition(task_id, TaskStatus.VALIDATING)
            source = self.uploads_dir / task_id / record.stored_filename
            if not source.is_file():
                raise AppError("UPLOAD_NOT_FOUND", "上传文件不存在")
            media_info = self.media_service.inspect(source, record.media_type)
            self.task_service.set_media_duration(task_id, media_info.duration_seconds)
            self._log_stage(task_id, TaskStatus.VALIDATING, stage_started)

            audio_path = source
            if record.media_type == MediaType.VIDEO:
                stage_started = perf_counter()
                self.task_service.transition(task_id, TaskStatus.EXTRACTING_AUDIO)
                audio_path = self.audio_dir / task_id / "audio.wav"
                self.media_service.extract_audio(source, audio_path)
                self._log_stage(task_id, TaskStatus.EXTRACTING_AUDIO, stage_started)

            stage_started = perf_counter()
            self.task_service.transition(task_id, TaskStatus.TRANSCRIBING)
            transcription = self.transcription_service.transcribe(audio_path)
            self._log_stage(task_id, TaskStatus.TRANSCRIBING, stage_started)
            stage_started = perf_counter()
            self.task_service.transition(task_id, TaskStatus.EXPORTING)
            result = TranscriptResult(
                task_id=task_id,
                original_filename=record.original_filename,
                media_type=record.media_type,
                language=transcription.language,
                duration_seconds=transcription.duration_seconds or media_info.duration_seconds,
                text=transcription.text,
                segments=transcription.segments,
                generated_at=datetime.now().astimezone(),
            )
            markdown_path, txt_path, result_path = self.export_service.export(result)
            artifacts = TaskArtifacts(
                markdown=str(markdown_path),
                txt=str(txt_path),
                result=str(result_path),
            )
            self.task_service.transition(task_id, TaskStatus.SUCCEEDED, artifacts=artifacts)
            self._log_stage(task_id, TaskStatus.EXPORTING, stage_started)
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

    @staticmethod
    def _log_stage(task_id: str, stage: TaskStatus, started_at: float) -> None:
        logger.info(
            "task_stage_completed task_id=%s stage=%s duration_seconds=%.3f",
            task_id,
            stage.value,
            perf_counter() - started_at,
        )
