from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    STAGE_MESSAGES,
    CreateTaskFromUrlRequest,
    MediaType,
    SourceType,
    TaskCreatedResponse,
    TaskRecord,
    TaskStatus,
    TaskStatusResponse,
    TranscriptResult,
    processing_method_label,
)
from backend.app.services.media_service import media_type_for_filename


router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _safe_display_filename(raw_filename: str | None) -> str:
    if not raw_filename:
        raise AppError("MISSING_FILE", "请选择一个音频或视频文件")
    normalized = raw_filename.replace("\\", "/").split("/")[-1].strip()
    if not normalized or len(normalized) > 255 or any(ord(char) < 32 for char in normalized):
        raise AppError("INVALID_FILENAME", "文件名无效，请重命名后重试")
    return normalized


def _artifact_path(request: Request, task_id: str, kind: str) -> tuple[Path, str]:
    task_service = request.app.state.task_service
    record = task_service.get(task_id)
    if record.status != TaskStatus.SUCCEEDED:
        raise AppError("RESULT_NOT_READY", "逐字稿尚未生成", status_code=409)
    raw_path = getattr(record.artifacts, kind, None)
    if not raw_path:
        raise AppError("ARTIFACT_NOT_FOUND", "输出文件不存在", status_code=404)
    path = Path(raw_path).resolve()
    outputs_dir = request.app.state.settings.outputs_dir.resolve()
    if not path.is_relative_to(outputs_dir) or not path.is_file():
        raise AppError("ARTIFACT_NOT_FOUND", "输出文件不存在", status_code=404)
    return path, record.original_filename


def _status_response(request: Request, record: TaskRecord) -> TaskStatusResponse:
    now = datetime.now().astimezone()
    terminal = record.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}
    elapsed_until = record.updated_at if terminal else now
    estimated_remaining: float | None = None
    if record.status == TaskStatus.TRANSCRIBING and record.media_duration_seconds:
        speed_factor = max(0.1, request.app.state.settings.transcription_speed_factor)
        in_stage = max(0.0, (now - record.updated_at).total_seconds())
        estimated_remaining = max(0.0, record.media_duration_seconds / speed_factor - in_stage)
    return TaskStatusResponse(
        task_id=record.task_id,
        status=record.status,
        stage_message=(record.error.message if record.error else STAGE_MESSAGES[record.status]),
        source_type=record.source_type,
        platform=record.platform,
        source_url=record.source_url,
        extract_method=record.extract_method,
        subtitle_kind=record.subtitle_kind,
        processing_method_label=processing_method_label(record.processing_method),
        created_at=record.created_at,
        updated_at=record.updated_at,
        elapsed_seconds=max(0.0, (elapsed_until - record.created_at).total_seconds()),
        media_duration_seconds=record.media_duration_seconds,
        estimated_remaining_seconds=estimated_remaining,
        error=record.error,
        artifacts={
            "markdown": f"/api/v1/tasks/{record.task_id}/download/markdown"
            if record.artifacts.markdown
            else None,
            "txt": f"/api/v1/tasks/{record.task_id}/download/txt"
            if record.artifacts.txt
            else None,
        },
    )


@router.post("/tasks", response_model=TaskCreatedResponse, status_code=202)
async def create_task(request: Request, file: UploadFile = File(...)) -> TaskCreatedResponse:
    settings = request.app.state.settings
    task_service = request.app.state.task_service
    filename = _safe_display_filename(file.filename)
    media_type = media_type_for_filename(filename)
    suffix = Path(filename).suffix.lower()
    task_id = task_service.new_task_id()
    upload_dir = settings.uploads_dir / task_id
    stored_filename = f"source{suffix}"
    destination = upload_dir / stored_filename
    upload_dir.mkdir(parents=True, exist_ok=False)

    size_bytes = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > settings.max_upload_bytes:
                    raise AppError(
                        "FILE_TOO_LARGE",
                        f"文件不能超过 {settings.max_upload_mb} MB",
                        status_code=413,
                    )
                handle.write(chunk)
        if size_bytes == 0:
            raise AppError("EMPTY_FILE", "上传文件为空")
        record = task_service.create(
            task_id=task_id,
            original_filename=filename,
            stored_filename=stored_filename,
            media_type=media_type,
            content_type=file.content_type,
            size_bytes=size_bytes,
        )
    except AppError:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise AppError(
            "UPLOAD_WRITE_FAILED",
            "文件保存失败，请检查磁盘空间后重试",
            status_code=503,
        ) from exc
    finally:
        await file.close()

    request.app.state.processor.submit(task_id)
    return TaskCreatedResponse(task_id=record.task_id, status=record.status)


@router.post("/tasks/from-url", response_model=TaskCreatedResponse, status_code=202)
def create_task_from_url(
    request: Request, payload: CreateTaskFromUrlRequest
) -> TaskCreatedResponse:
    """按链接创建任务。

    URL 的平台白名单校验在**创建任务之前**同步完成：非法链接直接返回 400，
    不会留下无意义的失败任务记录。
    """
    platform_service = request.app.state.platform_service
    task_service = request.app.state.task_service

    source = platform_service.resolve(payload.url)
    task_id = task_service.new_task_id()
    record = task_service.create(
        task_id=task_id,
        original_filename=source.video_id,
        stored_filename="",
        media_type=MediaType.VIDEO,
        content_type=None,
        size_bytes=0,
        source_type=SourceType.PLATFORM_URL,
        platform=source.platform,
        source_url=source.original_url,
        resolved_url=source.url,
    )
    request.app.state.processor.submit(task_id)
    return TaskCreatedResponse(task_id=record.task_id, status=record.status)


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(request: Request, task_id: UUID) -> TaskStatusResponse:
    return _status_response(request, request.app.state.task_service.get(str(task_id)))


@router.get("/tasks/{task_id}/result", response_model=TranscriptResult)
def get_result(request: Request, task_id: UUID) -> TranscriptResult:
    path, _ = _artifact_path(request, str(task_id), "result")
    return request.app.state.export_service.load_result(path)


@router.get("/tasks/{task_id}/download/markdown")
def download_markdown(request: Request, task_id: UUID) -> FileResponse:
    path, original_filename = _artifact_path(request, str(task_id), "markdown")
    stem = Path(original_filename).stem or "transcript"
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=f"{stem}.md")


@router.get("/tasks/{task_id}/download/txt")
def download_txt(request: Request, task_id: UUID) -> FileResponse:
    path, original_filename = _artifact_path(request, str(task_id), "txt")
    stem = Path(original_filename).stem or "transcript"
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=f"{stem}.txt")


@router.get("/config")
def get_public_config(request: Request) -> dict[str, int]:
    settings = request.app.state.settings
    return {
        "max_upload_mb": settings.max_upload_mb,
        "task_poll_interval_seconds": settings.task_poll_interval_seconds,
        "max_media_minutes": settings.max_media_minutes,
    }
