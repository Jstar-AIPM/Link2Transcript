from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.tasks import router as tasks_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError
from backend.app.core.logging import configure_logging
from backend.app.services.download_service import DownloadService
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.platform_service import PlatformService
from backend.app.services.processor import TaskProcessor
from backend.app.services.startup_service import run_startup_checks
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import TranscriptionService


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
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        run_startup_checks(app_settings, media_service, transcription_service)
        task_service.recover_incomplete()
        yield
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
    app.state.processor = processor

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数无效，请检查后重试",
                }
            },
        )

    app.include_router(tasks_router)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
