from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.tests.helpers import assert_user_copy


@pytest.fixture
def user_copy_checker():
    return assert_user_copy


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
        max_media_minutes=180,
        max_download_mb=1,
        bilibili_cookie="",
        platform_download_timeout_seconds=30,
        platform_rate_limit_kbps=None,
        platform_proxy="",
        transcription_speed_factor=4.0,
    )
