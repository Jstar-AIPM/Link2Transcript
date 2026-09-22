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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

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

    def ensure_directories(self) -> None:
        for directory in (
            self.uploads_dir,
            self.audio_dir,
            self.outputs_dir,
            self.tasks_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        data_dir=_resolve_data_dir(os.getenv("DATA_DIR", "./data")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "500")),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        task_poll_interval_seconds=int(os.getenv("TASK_POLL_INTERVAL_SECONDS", "2")),
        task_max_workers=max(1, int(os.getenv("TASK_MAX_WORKERS", "1"))),
    )

