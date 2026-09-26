from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    STAGE_MESSAGES,
    TERMINAL_STATUSES,
    CreateTaskFromUrlRequest,
    MediaType,
    SegmentItem,
    SourceType,
    TaskCreatedResponse,
    TaskRecord,
    TaskSegmentsResponse,
    TaskStatus,
    TaskStatusResponse,
    TranscriptResult,
    processing_method_label,
)
from backend.app.services.media_service import media_type_for_filename
from backend.app.services.segment_store import StoredSegment


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _owner_id(request: Request) -> str | None:
    """当前会话的任务归属标识；本地未强制登录时为 None（历史行为）。"""
    session = getattr(request.state, "session", None)
    return session.owner_id if session is not None else None


def _safe_display_filename(raw_filename: str | None) -> str:
    if not raw_filename:
        raise AppError("MISSING_FILE", "请选择一个音频或视频文件")
    normalized = raw_filename.replace("\\", "/").split("/")[-1].strip()
    if not normalized or len(normalized) > 255 or any(ord(char) < 32 for char in normalized):
        raise AppError("INVALID_FILENAME", "文件名无效，请重命名后重试")
    return normalized


def _owned_task(request: Request, task_id: str) -> TaskRecord:
    """取出任务并校验归属（阶段 6B）。

    强制登录时：任务必须有 owner_id 且与当前会话一致，否则 403。
    本地开发（未强制登录）时不做限制，行为与阶段 1–5 一致。
    """
    task_service = request.app.state.task_service
    record = task_service.get(task_id)
    if not request.app.state.settings.auth_required:
        return record
    session = getattr(request.state, "session", None)
    if session is None:
        raise AppError("AUTH_REQUIRED", "请先输入邀请码进入", status_code=401)
    if record.owner_id != session.owner_id:
        raise AppError("TASK_FORBIDDEN", "无权访问该任务", status_code=403)
    return record


def _artifact_path(request: Request, task_id: str, kind: str) -> tuple[Path, str]:
    record = _owned_task(request, task_id)
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


def _progress_percent(record: TaskRecord) -> float:
    """进度百分比：已转写时长 / 总时长。

    未完成时封顶 99，不给出“已完成”的错觉；成功后固定 100，
    不依赖片段是否覆盖到音频末尾的静音段。
    """
    if record.status == TaskStatus.SUCCEEDED:
        return 100.0
    duration = record.media_duration_seconds
    transcribed = record.transcribed_seconds
    if not duration or duration <= 0 or not transcribed:
        return 0.0
    return round(min(99.0, transcribed / duration * 100), 1)


def _enforce_daily_quota(request: Request) -> None:
    """每个邀请码每天的任务数上限（阶段 6.5）。

    与邀请码自带的「30 天 / 20 次登录」是两件事：那个限制的是登录次数，
    这里限制的是**提交任务的数量**（一个人登录一次仍可提交很多任务）。

    管理员码不受限制；未强制登录（本地开发）时不做限制；0 表示关闭。
    """
    settings = request.app.state.settings
    session = getattr(request.state, "session", None)
    limit = settings.max_tasks_per_code_per_day
    if session is None or session.is_admin or limit <= 0:
        return
    used = request.app.state.task_service.count_created_today(session.owner_id)
    if used >= limit:
        raise AppError(
            "DAILY_LIMIT_REACHED",
            f"今天的任务数量已达上限（{limit} 个），请明天再试",
            status_code=429,
        )


def _status_response(request: Request, record: TaskRecord) -> TaskStatusResponse:
    now = datetime.now().astimezone()
    terminal = record.status in TERMINAL_STATUSES
    queue_ahead = (
        request.app.state.task_service.pending_ahead(record.task_id)
        if record.status == TaskStatus.PENDING
        else 0
    )
    elapsed_until = record.updated_at if terminal else now
    estimated_remaining: float | None = None
    if record.status == TaskStatus.TRANSCRIBING and record.media_duration_seconds:
        speed_factor = max(0.1, request.app.state.settings.transcription_speed_factor)
        in_stage = max(0.0, (now - record.updated_at).total_seconds())
        estimated_remaining = max(0.0, record.media_duration_seconds / speed_factor - in_stage)
    if record.error is not None:
        stage_message = record.error.message
    else:
        # 细粒度文案（如“正在加载语音识别模型”）优先，缺省时回落粗粒度阶段文案。
        stage_message = record.stage_message or STAGE_MESSAGES[record.status]
    return TaskStatusResponse(
        task_id=record.task_id,
        status=record.status,
        stage_message=stage_message,
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
        segment_count=record.segment_count,
        transcribed_seconds=record.transcribed_seconds,
        progress_percent=_progress_percent(record),
        partial_result_available=record.partial_result_available,
        cancellable=not terminal,
        queue_ahead=queue_ahead,
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
    if not settings.enable_local_upload:
        # 线上环境不支持本地上传（平台请求体上限 16 MiB），给明确中文提示与替代方案
        raise AppError(
            "LOCAL_UPLOAD_DISABLED",
            "当前环境暂不支持上传本地文件，请改用 B 站视频链接",
            status_code=403,
        )
    task_service = request.app.state.task_service
    _enforce_daily_quota(request)
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
            owner_id=_owner_id(request),
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

    _enforce_daily_quota(request)
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
        owner_id=_owner_id(request),
    )
    request.app.state.processor.submit(task_id)
    return TaskCreatedResponse(task_id=record.task_id, status=record.status)


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(request: Request, task_id: UUID) -> TaskStatusResponse:
    return _status_response(request, _owned_task(request, str(task_id)))


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(request: Request, task_id: UUID) -> TaskStatusResponse:
    """取消任务（阶段 3D）。

    立即落到终态 ``cancelled``：工作线程会在下一个检查点停止，
    已落盘的部分结果保留，但不生成可下载的成功产物。
    """
    task_service = request.app.state.task_service
    _owned_task(request, str(task_id))
    record = task_service.request_cancel(str(task_id))
    # 取消响应里的进度要和磁盘对齐：用户下一秒就能读到已生成的内容。
    # （进度写回是按秒节流的，而工作线程要到下一个检查点才停止）
    request.app.state.processor.sync_partial_progress(record.task_id)
    record = task_service.get(record.task_id)
    logger.info("task_cancelled task_id=%s segment_count=%s", record.task_id, record.segment_count)
    return _status_response(request, record)


@router.get("/tasks/{task_id}/segments", response_model=TaskSegmentsResponse)
def get_task_segments(
    request: Request,
    task_id: UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> TaskSegmentsResponse:
    """增量拉取转写片段（阶段 3）。

    复用既有轮询：前端带上拉到的位置 ``after``，只取新增部分，避免长内容重复传输。
    """
    record = _owned_task(request, str(task_id))
    segments, total = _task_segments(request, record, after=after, limit=limit)
    next_after = after + len(segments)
    return TaskSegmentsResponse(
        task_id=record.task_id,
        status=record.status,
        partial=record.status != TaskStatus.SUCCEEDED,
        total=total,
        next_after=next_after,
        has_more=next_after < total,
        segments=[
            SegmentItem(index=s.index, start=s.start, end=s.end, text=s.text) for s in segments
        ],
    )


def _task_segments(
    request: Request, record: TaskRecord, *, after: int, limit: int
) -> tuple[list[StoredSegment], int]:
    store = request.app.state.segment_store
    stored = store.read(record.task_id, after=after, limit=limit)
    total = store.count(record.task_id)
    if total:
        return stored, total
    # 字幕路径不经过转写，没有逐段落盘；成功后片段直接从产物读取，
    # 让这个接口在“字幕”与“转写”两条链路上语义一致。
    if record.status == TaskStatus.SUCCEEDED and record.artifacts.result:
        result = request.app.state.export_service.load_result(Path(record.artifacts.result))
        items = [
            StoredSegment(index=index, start=s.start, end=s.end, text=s.text)
            for index, s in enumerate(result.segments)
        ]
        return items[after : after + limit], len(items)
    return [], 0


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
def get_public_config(request: Request) -> dict[str, int | bool | str]:
    settings = request.app.state.settings
    return {
        "max_upload_mb": settings.max_upload_mb,
        "task_poll_interval_seconds": settings.task_poll_interval_seconds,
        "max_media_minutes": settings.max_media_minutes,
        "enable_local_upload": settings.enable_local_upload,
        "require_auth": settings.auth_required,
        # B 站登录态：valid / invalid / unknown / not_configured
        # 失效时前端提示「链接任务会改用语音转写」，避免用户误以为卡住
        "bilibili_login": request.app.state.credential_service.status,
    }
