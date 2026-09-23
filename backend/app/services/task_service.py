from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from pydantic import ValidationError

from backend.app.core.errors import AppError, TaskNotFoundError
from backend.app.schemas.task import (
    CURRENT_SCHEMA_VERSION,
    ExtractMethod,
    MediaType,
    Platform,
    ProcessingMethod,
    SourceType,
    SubtitleKind,
    TaskArtifacts,
    TaskError,
    TaskRecord,
    TaskStatus,
)


logger = logging.getLogger(__name__)


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    # 说明：可续跑的任务由 ``requeue_for_resume`` 显式回到 pending，
    # 不走这张表（状态机本身不允许 transcribing → validating）。
    TaskStatus.PENDING: {TaskStatus.VALIDATING, TaskStatus.CHECKING_SUBTITLE, TaskStatus.FAILED},
    TaskStatus.VALIDATING: {
        TaskStatus.EXTRACTING_AUDIO,
        TaskStatus.TRANSCRIBING,
        TaskStatus.FAILED,
    },
    TaskStatus.CHECKING_SUBTITLE: {
        TaskStatus.DOWNLOADING_AUDIO,
        TaskStatus.EXPORTING,
        TaskStatus.FAILED,
    },
    TaskStatus.DOWNLOADING_AUDIO: {TaskStatus.TRANSCRIBING, TaskStatus.FAILED},
    TaskStatus.EXTRACTING_AUDIO: {TaskStatus.TRANSCRIBING, TaskStatus.FAILED},
    TaskStatus.TRANSCRIBING: {TaskStatus.EXPORTING, TaskStatus.FAILED},
    TaskStatus.EXPORTING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
}


class TaskService:
    def __init__(self, tasks_dir: Path) -> None:
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def new_task_id(self) -> str:
        return str(uuid4())

    def create(
        self,
        *,
        task_id: str,
        original_filename: str,
        stored_filename: str,
        media_type: MediaType,
        content_type: str | None,
        size_bytes: int,
        source_type: SourceType = SourceType.LOCAL_FILE,
        platform: Platform = Platform.LOCAL,
        source_url: str | None = None,
        resolved_url: str | None = None,
    ) -> TaskRecord:
        now = datetime.now().astimezone()
        record = TaskRecord(
            task_id=task_id,
            status=TaskStatus.PENDING,
            source_type=source_type,
            platform=platform,
            source_url=source_url,
            resolved_url=resolved_url,
            original_filename=original_filename,
            stored_filename=stored_filename,
            media_type=media_type,
            content_type=content_type,
            size_bytes=size_bytes,
            created_at=now,
            updated_at=now,
            progress_stage=TaskStatus.PENDING,
        )
        self._write(record)
        return record

    def get(self, task_id: str) -> TaskRecord:
        path = self._path(task_id)
        if not path.is_file():
            raise TaskNotFoundError()
        try:
            return TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise AppError(
                "TASK_RECORD_CORRUPTED",
                "任务记录损坏，无法读取",
                status_code=500,
            ) from exc

    def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        *,
        artifacts: TaskArtifacts | None = None,
    ) -> TaskRecord:
        with self._lock:
            record = self.get(task_id)
            if new_status not in ALLOWED_TRANSITIONS[record.status]:
                # 状态名（transcribing 等）是内部术语，不能出现在用户可见文案里。
                logger.warning(
                    "invalid_task_transition task_id=%s from=%s to=%s",
                    task_id,
                    record.status.value,
                    new_status.value,
                )
                raise AppError(
                    "INVALID_TASK_TRANSITION",
                    "任务状态异常，请重新发起任务",
                    status_code=500,
                )
            record.status = new_status
            record.progress_stage = new_status
            # 粗粒度阶段切换时清掉细粒度文案，避免成功后仍显示“正在生成逐字稿”。
            record.stage_message = None
            record.updated_at = datetime.now().astimezone()
            if artifacts is not None:
                record.artifacts = artifacts
            self._write(record)
            return record

    def set_media_duration(self, task_id: str, duration_seconds: float | None) -> TaskRecord:
        if duration_seconds is None:
            return self.get(task_id)
        return self._update(task_id, media_duration_seconds=max(0.0, duration_seconds))

    def set_downloaded_bytes(self, task_id: str, downloaded_bytes: int) -> TaskRecord:
        return self._update(task_id, downloaded_bytes=max(0, downloaded_bytes))

    def set_original_filename(self, task_id: str, original_filename: str) -> TaskRecord:
        return self._update(task_id, original_filename=original_filename)

    def set_stage_message(self, task_id: str, stage_message: str | None) -> TaskRecord:
        """设置细粒度阶段文案（阶段 3）。传 ``None`` 表示回落默认阶段文案。"""
        return self._update(task_id, stage_message=stage_message)

    def set_progress(
        self,
        task_id: str,
        *,
        segment_count: int,
        transcribed_seconds: float,
        stage_message: str | None = None,
    ) -> TaskRecord:
        """写回转写进度。

        调用方（处理器）会节流，不逐片段调用：任务记录是整体原子替换写入，
        数千次重写会造成明显的写放大。
        """
        changes: dict[str, object] = {
            "segment_count": max(0, int(segment_count)),
            "transcribed_seconds": max(0.0, float(transcribed_seconds)),
        }
        if stage_message is not None:
            changes["stage_message"] = stage_message
        return self._update(task_id, **changes)

    def mark_partial_result(self, task_id: str, *, available: bool = True) -> TaskRecord:
        """标记这是一份保留的部分结果（失败时已生成的片段不丢弃）。"""
        return self._update(task_id, partial_result_available=available)

    def requeue_for_resume(self, task_id: str) -> TaskRecord:
        """把有进度的任务放回队列，交给处理器从断点续写（阶段 3B）。

        为什么显式回到 ``pending``：状态机不允许 ``transcribing → validating``，
        而续跑确实需要重新走校验 / 下载 / 提取这些前置步骤（真正省下来的是转写本身），
        因此回到链路起点重新开始，同时**保留已落盘的片段与进度**。
        """
        with self._lock:
            record = self.get(task_id)
            record.status = TaskStatus.PENDING
            record.progress_stage = TaskStatus.PENDING
            record.stage_message = None
            record.updated_at = datetime.now().astimezone()
            self._write(record)
            return record

    def increment_resumed_count(self, task_id: str) -> TaskRecord:
        """记录本任务被续写的次数（排查异常反复续写用）。"""
        record = self.get(task_id)
        return self._update(task_id, resumed_count=record.resumed_count + 1)

    def set_extraction(
        self,
        task_id: str,
        *,
        processing_method: ProcessingMethod,
        extract_method: ExtractMethod | None = None,
        subtitle_kind: SubtitleKind | None = None,
    ) -> TaskRecord:
        return self._update(
            task_id,
            processing_method=processing_method,
            extract_method=extract_method,
            subtitle_kind=subtitle_kind,
        )

    def fail(
        self,
        task_id: str,
        *,
        code: str,
        message: str,
        internal_type: str | None = None,
    ) -> TaskRecord:
        with self._lock:
            record = self.get(task_id)
            if record.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                return record
            failed_stage = record.status
            record.status = TaskStatus.FAILED
            record.progress_stage = TaskStatus.FAILED
            record.stage_message = None
            record.updated_at = datetime.now().astimezone()
            record.error = TaskError(
                code=code,
                message=message,
                internal_type=internal_type,
                failed_stage=failed_stage,
            )
            self._write(record)
            return record

    def recover_incomplete(
        self, *, is_resumable: Callable[[TaskRecord], bool] | None = None
    ) -> tuple[int, list[TaskRecord]]:
        """服务重启后的收尾。

        返回 ``(标记为中断的任务数, 需要重新入队续跑的任务)``。

        - ``is_resumable``：由调用方判断“这个任务还能不能接着跑”（只有它知道
          磁盘上有没有已落盘片段、音频是否可复用）。缺省为 ``None``，表示不做
          续跑，行为与阶段 1/2 完全一致：所有非终态任务都标记 ``TASK_INTERRUPTED``；
        - 被判定可续跑的任务**保持原状态**，不改写为失败，由调用方重新入队。
        """
        interrupted = 0
        resumable: list[TaskRecord] = []
        for path in self.tasks_dir.glob("*.json"):
            try:
                record = self.get(path.stem)
            except AppError:
                continue
            if record.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                continue
            if is_resumable is not None and is_resumable(record):
                resumable.append(record)
                continue
            self.fail(
                record.task_id,
                code="TASK_INTERRUPTED",
                message="服务曾中断，请重新上传文件发起任务",
                internal_type="ProcessRestart",
            )
            interrupted += 1
        return interrupted, resumable

    # ------------------------------------------------------------------ 内部

    def _update(self, task_id: str, **changes) -> TaskRecord:
        with self._lock:
            record = self.get(task_id)
            for field, value in changes.items():
                setattr(record, field, value)
            record.updated_at = datetime.now().astimezone()
            self._write(record)
            return record

    def _path(self, task_id: str) -> Path:
        try:
            normalized = str(UUID(str(task_id)))
        except ValueError as exc:
            raise TaskNotFoundError() from exc
        return self.tasks_dir / f"{normalized}.json"

    def _write(self, record: TaskRecord) -> None:
        target = self._path(record.task_id)
        # 惰性迁移：任何一次写入都把记录升到当前 schema 版本。
        record.schema_version = CURRENT_SCHEMA_VERSION
        payload = record.model_dump_json(indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=f".{record.task_id}.", dir=self.tasks_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
