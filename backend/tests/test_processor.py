from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType, TaskStatus, TranscriptSegment
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaInfo
from backend.app.services.processor import TaskProcessor
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import Transcription


class FakeMediaService:
    def inspect(self, source: Path, expected_type: MediaType) -> MediaInfo:
        assert source.is_file()
        return MediaInfo(duration_seconds=2.0, has_audio=True, has_video=expected_type == MediaType.VIDEO)

    def extract_audio(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")
        return destination


class FakeTranscriptionService:
    def transcribe(self, audio_path: Path) -> Transcription:
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


def create_processor_task(settings, media_type: MediaType):
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


def test_video_processor_runs_complete_chain(settings):
    tasks, task_id = create_processor_task(settings, MediaType.VIDEO)
    processor = TaskProcessor(
        task_service=tasks,
        media_service=FakeMediaService(),
        transcription_service=FakeTranscriptionService(),
        export_service=ExportService(settings.outputs_dir),
        uploads_dir=settings.uploads_dir,
        audio_dir=settings.audio_dir,
    )
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
            FakeTranscriptionService(),
            None,
            "AUDIO_EXTRACTION_FAILED",
            TaskStatus.EXTRACTING_AUDIO,
        ),
        (
            MediaType.AUDIO,
            FakeMediaService(),
            FailingTranscriptionService(),
            None,
            "TRANSCRIPTION_FAILED",
            TaskStatus.TRANSCRIBING,
        ),
        (
            MediaType.AUDIO,
            FakeMediaService(),
            FakeTranscriptionService(),
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
    tasks, task_id = create_processor_task(settings, media_type)
    processor = TaskProcessor(
        task_service=tasks,
        media_service=media_service,
        transcription_service=transcription_service,
        export_service=export_service or ExportService(settings.outputs_dir),
        uploads_dir=settings.uploads_dir,
        audio_dir=settings.audio_dir,
    )
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == error_code
    assert record.error.failed_stage == failed_stage
