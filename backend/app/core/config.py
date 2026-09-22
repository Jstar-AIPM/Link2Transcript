from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_data_dir(raw_value: str) -> Path:
    path = Path(raw_value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    app_env: str
    data_dir: Path
    max_upload_mb: int
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    task_poll_interval_seconds: int
    task_max_workers: int
    max_media_minutes: int = 0
    max_download_mb: int = 1024
    bilibili_cookie: str = ""
    platform_download_timeout_seconds: int = 1800
    platform_rate_limit_kbps: int | None = None
    platform_proxy: str = ""
    transcription_speed_factor: float = 4.0

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def max_media_seconds(self) -> float:
        """内容时长上限。``max_media_minutes`` 为 0 表示**不限制时长**（默认）。"""
        if self.max_media_minutes <= 0:
            return float("inf")
        return float(self.max_media_minutes * 60)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def session_dir(self) -> Path:
        """登录凭据等敏感运行态文件的存放目录（不进入版本控制）。"""
        return self.data_dir / ".session"

    def ensure_directories(self) -> None:
        for directory in (
            self.uploads_dir,
            self.audio_dir,
            self.outputs_dir,
            self.tasks_dir,
            self.downloads_dir,
            self.session_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=_resolve_data_dir(os.getenv("DATA_DIR", "./data")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "2048")),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        task_poll_interval_seconds=int(os.getenv("TASK_POLL_INTERVAL_SECONDS", "2")),
        task_max_workers=max(1, int(os.getenv("TASK_MAX_WORKERS", "1"))),
        max_media_minutes=int(os.getenv("MAX_MEDIA_MINUTES", "0")),
        max_download_mb=int(os.getenv("MAX_DOWNLOAD_MB", "1024")),
        bilibili_cookie=os.getenv("BILIBILI_COOKIE", "").strip(),
        platform_download_timeout_seconds=int(
            os.getenv("PLATFORM_DOWNLOAD_TIMEOUT_SECONDS", "1800")
        ),
        platform_rate_limit_kbps=_optional_int("PLATFORM_RATE_LIMIT_KBPS"),
        platform_proxy=os.getenv("PLATFORM_PROXY", "").strip(),
        transcription_speed_factor=float(os.getenv("TRANSCRIPTION_SPEED_FACTOR", "4")),
    )
