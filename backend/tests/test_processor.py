from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType, TaskStatus, TranscriptSegment
from backend.app.services.download_service import DownloadService
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaInfo
from backend.app.services.processor import TaskProcessor
from backend.app.services.segment_store import SegmentStore
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import Transcription


class FakeMediaService:
    def __init__(self, duration_seconds: float | None = 2.0) -> None:
        self.duration_seconds = duration_seconds
        self.slices: list[tuple[Path, float]] = []

    def inspect(self, source: Path, expected_type: MediaType) -> MediaInfo:
        assert source.is_file()
        return MediaInfo(
            duration_seconds=self.duration_seconds,
            has_audio=True,
            has_video=expected_type == MediaType.VIDEO,
        )

    def extract_audio(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")
        return destination

    def slice_audio(self, source: Path, destination: Path, start_seconds: float) -> Path:
        """断点续写用的截取（阶段 3B）：记录请求的起点，供断言校验。"""
        assert source.is_file()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")
        self.slices.append((destination, start_seconds))
        return destination


class FakeTranscriptionService:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path, **kwargs) -> Transcription:
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


class FailingSliceMediaService(FakeMediaService):
    def slice_audio(self, source: Path, destination: Path, start_seconds: float) -> Path:
        raise AppError("AUDIO_SLICE_FAILED", "未能续写已中断的任务")


class FailingTranscriptionService:
    def transcribe(self, audio_path: Path, **kwargs) -> Transcription:
        raise AppError("TRANSCRIPTION_FAILED", "转写未完成，请重试")


class SilentTranscriptionService:
    """模拟“内容里没有人声”：服务层抛出通用提示，由编排层换成具体说法。"""

    def transcribe(self, audio_path: Path, **kwargs) -> Transcription:
        raise AppError("SILENT_AUDIO", "未检测到人声内容")


class LongMediaService(FakeMediaService):
    def __init__(self, duration_seconds: float = 20_000.0) -> None:
        super().__init__(duration_seconds=duration_seconds)


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
    segment_store=None,
    progress_persist_interval_seconds=1.0,
    resume_overlap_seconds=2.0,
):
    return TaskProcessor(
        task_service=TaskService(settings.tasks_dir),
        media_service=media_service or FakeMediaService(),
        transcription_service=transcription_service or FakeTranscriptionService(),
        export_service=export_service or ExportService(settings.outputs_dir),
        download_service=download_service or StubDownloadService(),
        subtitle_service=SubtitleService(),
        segment_store=segment_store or SegmentStore(settings.outputs_dir),
        progress_persist_interval_seconds=progress_persist_interval_seconds,
        resume_overlap_seconds=resume_overlap_seconds,
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
    """限制被重新启用时（MAX_MEDIA_MINUTES 设为正整数）依然生效。"""
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    processor = build_processor(settings)
    processor.media_service = LongMediaService()
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "VIDEO_TOO_LONG"
    assert record.error.failed_stage == TaskStatus.VALIDATING
    user_copy_checker(record.error.message)


def test_duration_limit_is_disabled_by_default(settings):
    """默认不限制内容时长：4-5 小时的播客也应该正常跑完。"""
    unlimited = dataclasses.replace(settings, max_media_minutes=0)
    assert unlimited.max_media_seconds == float("inf")

    tasks, task_id = create_local_task(unlimited, MediaType.AUDIO)
    processor = build_processor(unlimited)
    processor.media_service = LongMediaService(duration_seconds=5 * 3600)
    processor.process(task_id)
    processor.shutdown()

    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.media_duration_seconds == 5 * 3600


def test_no_speech_in_local_file_uses_file_wording(settings, user_copy_checker):
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    processor = build_processor(settings, transcription_service=SilentTranscriptionService())
    processor.process(task_id)
    processor.shutdown()
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "SILENT_AUDIO"
    assert "文件中" in record.error.message
    assert "请确认文件是否正确" in record.error.message
    user_copy_checker(record.error.message)
