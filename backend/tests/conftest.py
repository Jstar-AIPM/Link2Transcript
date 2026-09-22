from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        data_dir=tmp_path / "data",
        max_upload_mb=1,
        whisper_model="tiny",
        whisper_device="cpu",
        whisper_compute_type="int8",
        task_poll_interval_seconds=1,
        task_max_workers=1,
    )

