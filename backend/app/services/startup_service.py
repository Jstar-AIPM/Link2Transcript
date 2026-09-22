from __future__ import annotations

import logging
import os
import tempfile

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.services.media_service import MediaService
from backend.app.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)


def run_startup_checks(
    settings: Settings,
    media_service: MediaService,
    transcription_service: TranscriptionService,
) -> None:
    settings.ensure_directories()
    try:
        fd, probe_path = tempfile.mkstemp(prefix=".write-check-", dir=settings.data_dir)
        os.close(fd)
        os.unlink(probe_path)
    except OSError as exc:
        raise AppError(
            "DATA_DIRECTORY_UNWRITABLE",
            "数据目录不可写，请检查目录权限和磁盘空间",
            status_code=503,
        ) from exc
    media_service.ensure_tools()
    transcription_service.check_runtime()
    _check_platform_runtime(settings)


def _check_platform_runtime(settings: Settings) -> None:
    """链接链路依赖 yt-dlp。缺失时给出明确提示，而不是等到用户提交链接才失败。"""
    try:
        import yt_dlp
    except Exception as exc:
        raise AppError(
            "PLATFORM_RUNTIME_UNAVAILABLE",
            "链接解析组件不可用，请重新安装项目依赖",
            status_code=503,
        ) from exc
    logger.info(
        "platform_runtime_ready yt_dlp=%s cookie_configured=%s max_media_minutes=%s",
        getattr(getattr(yt_dlp, "version", None), "__version__", "unknown"),
        bool(settings.bilibili_cookie),
        settings.max_media_minutes,
    )
