from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType, TaskStatus, TranscriptSegment
from backend.app.services.download_service import DownloadService
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaInfo
from backend.app.services.processor import TaskProcessor
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import Transcription


class FakeMediaService:
    def inspect(self, source: Path, expected_type: MediaType) -> MediaInfo:
        assert source.is_file()
        return MediaInfo(
            duration_seconds=2.0, has_audio=True, has_video=expected_type == MediaType.VIDEO
        )

    def extract_audio(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")
        return destination


class FakeTranscriptionService:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> Transcription:
        self.calls.append(audio_path)
        assert audio_path.is_file()
        return Transcription(
            text="测试内容",
            segments=[TranscriptSegment(start=0, end=2, text="测试内容")],
            language="zh",
            duration_seconds=2.0,
        )


class FailingExtractionMediaService(FakeMediaService):
    def extract_audio(self, source: Path, destination: Path) -> Path:
        raise AppError("AUDIO_EXTRACTION_FAILED", "未能从视频中提取音频")


class FailingTranscriptionService:
    def transcribe(self, audio_path: Path) -> Transcription:
        raise AppError("TRANSCRIPTION_FAILED", "转写未完成，请重试")


class FailingExportService:
    def export(self, result):
        raise AppError("EXPORT_FAILED", "逐字稿文件生成失败")


class StubLogger:
    subtitle_login_required = False
    messages: list[str] = []


class StubDownloadService(DownloadService):
    """离线替身：不访问网络，直接返回预设的元信息与下载产物。"""

    def __init__(self, *, info: dict | None = None, error: AppError | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._info = info or {}
        self._error = error
        self.probe_calls: list[str] = []
        self.download_calls: list[str] = []

    def _extract(self, url, *, download, options=None, listsubtitles=False, noplaylist=False):
        if self._error is not None:
            raise self._error
        return dict(self._info), StubLogger()

    def download_audio(self, url, destination_dir: Path, task_id: str):
        from backend.app.services.download_service import DownloadedAudio

        self.download_calls.append(url)
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / "audio.m4a"
        path.write_bytes(b"audio-bytes")
        return DownloadedAudio(path=path, size_bytes=path.stat().st_size)


def create_local_task(settings, media_type: MediaType):
    settings.ensure_directories()
    tasks = TaskService(settings.tasks_dir)
    task_id = tasks.new_task_id()
    suffix = ".mp4" if media_type == MediaType.VIDEO else ".mp3"
    stored_filename = f"source{suffix}"
    upload_dir = settings.uploads_dir / task_id
    upload_dir.mkdir(parents=True)
    (upload_dir / stored_filename).write_bytes(b"media")
    tasks.create(
        task_id=task_id,
        original_filename=f"demo{suffix}",
        stored_filename=stored_filename,
        media_type=media_type,
        content_type="video/mp4" if media_type == MediaType.VIDEO else "audio/mpeg",
        size_bytes=5,
    )
    return tasks, task_id


def build_processor(
    settings,
    *,
    media_service=None,
    transcription_service=None,
    export_service=None,
    download_service=None,
):
    return TaskProcessor(
        task_service=TaskService(settings.tasks_dir),
        media_service=media_service or FakeMediaService(),
        transcription_service=transcription_service or FakeTranscriptionService(),
        export_service=export_service or ExportService(settings.outputs_dir),
        download_service=download_service or StubDownloadService(),
        subtitle_service=SubtitleService(),
        uploads_dir=settings.uploads_dir,
        audio_dir=settings.audio_dir,
        downloads_dir=settings.downloads_dir,
        max_media_seconds=settings.max_media_seconds,
        max_media_minutes=settings.max_media_minutes,
    )


def test_video_processor_runs_complete_chain(settings):
    tasks, task_id = create_local_task(settings, MediaType.VIDEO)
    processor = build_processor(settings)
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert Path(record.artifacts.markdown).is_file()
    assert Path(record.artifacts.txt).is_file()
    assert (settings.audio_dir / task_id / "audio.wav").is_file()
    assert record.media_duration_seconds == 2.0


@pytest.mark.parametrize(
    ("media_type", "media_service", "transcription_service", "export_service", "error_code", "failed_stage"),
    [
        (
            MediaType.VIDEO,
            FailingExtractionMediaService(),
            None,
            None,
            "AUDIO_EXTRACTION_FAILED",
            TaskStatus.EXTRACTING_AUDIO,
        ),
        (
            MediaType.AUDIO,
            None,
            FailingTranscriptionService(),
            None,
            "TRANSCRIPTION_FAILED",
            TaskStatus.TRANSCRIBING,
        ),
        (
            MediaType.AUDIO,
            None,
            None,
            FailingExportService(),
            "EXPORT_FAILED",
            TaskStatus.EXPORTING,
        ),
    ],
)
def test_processor_failures_enter_failed_state(
    settings,
    media_type,
    media_service,
    transcription_service,
    export_service,
    error_code,
    failed_stage,
):
    tasks, task_id = create_local_task(settings, media_type)
    processor = build_processor(
        settings,
        media_service=media_service,
        transcription_service=transcription_service,
        export_service=export_service,
    )
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == error_code
    assert record.error.failed_stage == failed_stage


def test_local_file_exceeding_duration_limit_fails(settings, user_copy_checker):
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    processor = build_processor(settings)

    class LongMediaService(FakeMediaService):
        def inspect(self, source, expected_type):
            return MediaInfo(duration_seconds=20_000.0, has_audio=True, has_video=False)

    processor.media_service = LongMediaService()
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "VIDEO_TOO_LONG"
    assert record.error.failed_stage == TaskStatus.VALIDATING
    user_copy_checker(record.error.message)
