from __future__ import annotations

import os
import tempfile

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.services.media_service import MediaService
from backend.app.services.transcription_service import TranscriptionService


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
