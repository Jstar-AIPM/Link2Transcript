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
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.processor import TaskProcessor
from backend.app.services.startup_service import run_startup_checks
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
    processor = TaskProcessor(
        task_service=task_service,
        media_service=media_service,
        transcription_service=transcription_service,
        export_service=export_service,
        uploads_dir=app_settings.uploads_dir,
        audio_dir=app_settings.audio_dir,
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
