from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.auth import router as auth_router
from backend.app.api.tasks import router as tasks_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError
from backend.app.core.logging import configure_logging
from backend.app.core.trace import get_trace_id, new_trace_id, set_trace_id
from backend.app.services.cleanup_service import CleanupService
from backend.app.services.backup_service import BackupScheduler, BackupService
from backend.app.services.bilibili_api_service import BilibiliApiService
from backend.app.services.credential_service import BilibiliCredentialService
from backend.app.services.download_service import DownloadService
from backend.app.services.invite_service import InviteService, bearer_token
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.platform_service import PlatformService
from backend.app.services.processor import TaskProcessor
from backend.app.services.segment_store import SegmentStore
from backend.app.services.startup_service import run_startup_checks
from backend.app.services.storage_service import build_storage
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)

def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    configure_logging()

    task_service = TaskService(app_settings.tasks_dir)
    media_service = MediaService()
    transcription_service = TranscriptionService(
        app_settings.whisper_model,
        app_settings.whisper_device,
        app_settings.whisper_compute_type,
    )
    export_service = ExportService(app_settings.outputs_dir)
    platform_service = PlatformService(
        timeout_seconds=min(60, app_settings.platform_download_timeout_seconds),
        proxy=app_settings.platform_proxy,
    )
    download_service = DownloadService(
        cookie=app_settings.bilibili_cookie,
        cookie_file_dir=app_settings.session_dir,
        proxy=app_settings.platform_proxy,
        max_media_seconds=app_settings.max_media_seconds,
        max_media_minutes=app_settings.max_media_minutes,
        max_download_bytes=app_settings.max_download_bytes,
        rate_limit_kbps=app_settings.platform_rate_limit_kbps,
    )
    subtitle_service = SubtitleService()
    # 链接链路：默认走 B 站 API（机房 IP 不会被 www.bilibili.com 的 412 风控挡住），
    # 需要时可切回 yt-dlp（PLATFORM_BACKEND=ytdlp）
    if app_settings.platform_backend == "ytdlp":
        platform_media_service = download_service
    else:
        platform_media_service = BilibiliApiService(
            cookie=app_settings.bilibili_cookie,
            cookie_file_dir=app_settings.session_dir,
            harvest_device_cookies=True,
            max_media_seconds=app_settings.max_media_seconds,
            max_media_minutes=app_settings.max_media_minutes,
        )
    # 阶段 6：B 站登录态自检（字幕路径依赖它；失效时后台告警，不阻塞启动）
    credential_service = BilibiliCredentialService(app_settings.bilibili_cookie)
    invite_service = InviteService(
        app_settings.invite_store_path,
        admin_code=app_settings.admin_invite_code,
        valid_days=app_settings.invite_valid_days,
        max_uses=app_settings.invite_max_uses,
    )
    # 阶段 6：对象存储（线上备份业务数据；未配置时为空操作，本地行为不变）
    storage = build_storage(app_settings)
    backup_service = BackupService(data_dir=app_settings.data_dir, storage=storage)
    backup_scheduler = BackupScheduler(backup_service, app_settings.backup_interval_seconds)
    cleanup_service = CleanupService(
        data_dir=app_settings.data_dir,
        task_service=task_service,
        storage=storage,
        retention_days=app_settings.retention_days,
        backup_service=backup_service,
    )
    segment_store = SegmentStore(
        app_settings.outputs_dir, max_segments=app_settings.max_segments_per_task
    )
    processor = TaskProcessor(
        task_service=task_service,
        media_service=media_service,
        transcription_service=transcription_service,
        export_service=export_service,
        download_service=download_service,
        subtitle_service=subtitle_service,
        uploads_dir=app_settings.uploads_dir,
        audio_dir=app_settings.audio_dir,
        downloads_dir=app_settings.downloads_dir,
        max_media_seconds=app_settings.max_media_seconds,
        max_media_minutes=app_settings.max_media_minutes,
        max_workers=app_settings.task_max_workers,
        segment_store=segment_store,
        backup_service=backup_service,
        progress_persist_interval_seconds=app_settings.progress_persist_interval_seconds,
        resume_overlap_seconds=app_settings.resume_overlap_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        run_startup_checks(app_settings, media_service, transcription_service)
        # 先把云端已有的业务数据恢复回本地（/tmp 可能是新的实例）
        backup_service.restore_missing()
        # 清理超期产物（RETENTION_DAYS=0 时不做任何事）
        cleanup_service.cleanup_expired()
        backup_scheduler.start()
        credential_service.verify_in_background()
        # 有已落盘片段的任务可以接着跑（阶段 3B），不必从零重来；
        # 没有进度的任务沿用阶段 1 行为：标记中断失败。
        is_resumable = (
            (lambda record: segment_store.has_content(record.task_id))
            if app_settings.resume_on_startup
            else None
        )
        interrupted, resumable = task_service.recover_incomplete(is_resumable=is_resumable)
        if interrupted:
            logger.info("tasks_marked_interrupted count=%s", interrupted)
        for record in resumable:
            # 状态机不允许从 transcribing 回到 validating，这里显式回到 pending
            # 重新走链路；已落盘片段与进度保留，转写阶段会从断点续写。
            task_service.requeue_for_resume(record.task_id)
            logger.info(
                "task_resume_enqueued task_id=%s from_status=%s segment_count=%s",
                record.task_id,
                record.status.value,
                record.segment_count,
            )
            processor.submit(record.task_id)
        yield
        backup_scheduler.stop()
        processor.shutdown()

    app = FastAPI(title="逐字稿提取器", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.task_service = task_service
    app.state.media_service = media_service
    app.state.transcription_service = transcription_service
    app.state.export_service = export_service
    app.state.platform_service = platform_service
    app.state.download_service = download_service
    app.state.subtitle_service = subtitle_service
    app.state.segment_store = segment_store
    app.state.processor = processor
    app.state.invite_service = invite_service
    app.state.platform_media_service = platform_media_service
    app.state.credential_service = credential_service
    app.state.storage = storage
    app.state.backup_service = backup_service
    app.state.cleanup_service = cleanup_service

    PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/config"}

    @app.middleware("http")
    async def trace_and_auth_middleware(request: Request, call_next):
        """追踪号 + 会话解析（是否强制登录由配置决定）。

        这里只做"解析并放进 request.state"，具体某个接口要不要求登录由接口自己声明：
        这样"公开接口"与"需登录接口"的边界写在路由旁边，一眼可见。
        """
        set_trace_id(new_trace_id())
        session = invite_service.resolve(bearer_token(request.headers.get("Authorization")))
        request.state.session = session

        path = request.url.path
        needs_auth = (
            app_settings.auth_required
            and path.startswith("/api/v1/")
            and path not in PUBLIC_PATHS
        )
        if needs_auth and session is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "AUTH_REQUIRED",
                        "message": "请先输入邀请码进入",
                        "trace_id": get_trace_id(),
                    }
                },
                headers={"X-Trace-Id": get_trace_id()},
            )

        response = await call_next(request)
        response.headers["X-Trace-Id"] = get_trace_id()
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "trace_id": get_trace_id(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数无效，请检查后重试",
                    "trace_id": get_trace_id(),
                }
            },
        )

    app.include_router(auth_router)
    app.include_router(tasks_router)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
