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


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


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
    max_media_minutes: int = 360
    #: 是否允许上传本地文件。线上（veFaaS）同步调用请求体上限 16 MiB，
    #: 与本地 2 GB 上传冲突，因此线上关闭；本地开发默认开启。
    enable_local_upload: bool = True
    max_download_mb: int = 1024
    bilibili_cookie: str = ""
    platform_download_timeout_seconds: int = 1800
    platform_rate_limit_kbps: int | None = None
    platform_proxy: str = ""
    transcription_speed_factor: float = 4.0
    # 阶段 3：转写过程实时反馈
    progress_persist_interval_seconds: float = 1.0
    resume_on_startup: bool = True
    resume_overlap_seconds: float = 2.0
    max_segments_per_task: int = 200_000

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def max_media_seconds(self) -> float:
        """内容时长上限。``max_media_minutes`` 为 0 表示不限制时长。

        默认 360 分钟（6 小时）：足够覆盖 4–5 小时的播客，同时挡住夸张输入。
        """
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
        enable_local_upload=_bool("ENABLE_LOCAL_UPLOAD", True),
        max_media_minutes=int(os.getenv("MAX_MEDIA_MINUTES", "360")),
        max_download_mb=int(os.getenv("MAX_DOWNLOAD_MB", "1024")),
        bilibili_cookie=os.getenv("BILIBILI_COOKIE", "").strip(),
        platform_download_timeout_seconds=int(
            os.getenv("PLATFORM_DOWNLOAD_TIMEOUT_SECONDS", "1800")
        ),
        platform_rate_limit_kbps=_optional_int("PLATFORM_RATE_LIMIT_KBPS"),
        platform_proxy=os.getenv("PLATFORM_PROXY", "").strip(),
        transcription_speed_factor=float(os.getenv("TRANSCRIPTION_SPEED_FACTOR", "4")),
        progress_persist_interval_seconds=float(
            os.getenv("PROGRESS_PERSIST_INTERVAL_SECONDS", "1")
        ),
        resume_on_startup=_bool("RESUME_ON_STARTUP", True),
        resume_overlap_seconds=float(os.getenv("RESUME_OVERLAP_SECONDS", "2")),
        max_segments_per_task=int(os.getenv("MAX_SEGMENTS_PER_TASK", "200000")),
    )
