from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    ExtractMethod,
    MediaType,
    Platform,
    ProcessingMethod,
    SourceType,
    SubtitleKind,
    TaskStatus,
)
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.export_service import ExportService

from backend.tests.test_processor import (
    FakeMediaService,
    FakeTranscriptionService,
    build_processor,
)
from backend.app.services.download_service import DownloadedAudio


SRT = "1\n00:00:00,000 --> 00:00:01,500\n字幕第一句\n\n2\n00:00:01,500 --> 00:00:03,000\n字幕第二句\n"

VIDEO_URL = "https://www.bilibili.com/video/BV1BqhB6nEdN"


def make_probe_info(**overrides) -> dict:
    info = {
        "id": "BV1BqhB6nEdN",
        "title": "测试视频/非法:标题?",
        "duration": 174.0,
        "webpage_url": VIDEO_URL,
        "extractor": "BiliBili",
    }
    info.update(overrides)
    return info


class PlatformDownloadStub:
    def __init__(self, *, info=None, probe_error=None, download_error=None):
        self.info = info if info is not None else make_probe_info()
        self.probe_error = probe_error
        self.download_error = download_error
        self.downloaded: list[str] = []

    def probe(self, url: str):
        if self.probe_error:
            raise self.probe_error
        from backend.app.services.download_service import VideoMeta

        return VideoMeta(
            video_id=self.info.get("id", ""),
            title=self.info.get("title", ""),
            duration_seconds=self.info.get("duration"),
            webpage_url=self.info.get("webpage_url", url),
            subtitles=self.info.get("subtitles") or {},
            automatic_captions=self.info.get("automatic_captions") or {},
            subtitle_needs_login=self.info.get("subtitle_needs_login", False),
            extractor=self.info.get("extractor", "BiliBili"),
        )

    def download_audio(self, url: str, destination_dir: Path, task_id: str):
        if self.download_error:
            raise self.download_error
        self.downloaded.append(url)
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / "audio.m4a"
        path.write_bytes(b"audio-bytes")
        return DownloadedAudio(path=path, size_bytes=path.stat().st_size)


def create_platform_task(settings):
    settings.ensure_directories()
    tasks = TaskService(settings.tasks_dir)
    task_id = tasks.new_task_id()
    tasks.create(
        task_id=task_id,
        original_filename="BV1BqhB6nEdN",
        stored_filename="",
        media_type=MediaType.VIDEO,
        content_type=None,
        size_bytes=0,
        source_type=SourceType.PLATFORM_URL,
        platform=Platform.BILIBILI,
        source_url=VIDEO_URL,
        resolved_url=VIDEO_URL,
    )
    return tasks, task_id


def run(settings, download_stub, transcription_service=None, export_service=None, media_service=None):
    tasks, task_id = create_platform_task(settings)
    processor = build_processor(
        settings,
        media_service=media_service or FakeMediaService(duration_seconds=174.0),
        download_service=download_stub,
        transcription_service=transcription_service,
        export_service=export_service,
    )
    processor.process(task_id)
    processor.shutdown()
    return tasks.get(task_id)


def test_subtitle_path_succeeds_without_calling_whisper(settings):
    transcription = FakeTranscriptionService()
    record = run(
        settings,
        PlatformDownloadStub(info=make_probe_info(subtitles={"zh-Hans": [{"ext": "srt", "data": SRT}]})),
        transcription_service=transcription,
    )
    assert record.status == TaskStatus.SUCCEEDED
    assert record.extract_method == ExtractMethod.SUBTITLE
    assert record.subtitle_kind == SubtitleKind.CC
    assert record.processing_method == ProcessingMethod.SUBTITLE_BILIBILI_CC
    assert transcription.calls == [], "有字幕时不应调用语音识别"
    markdown = Path(record.artifacts.markdown).read_text(encoding="utf-8")
    assert "字幕提取（人工字幕）" in markdown
    assert "原始地址：https://www.bilibili.com/video/BV1BqhB6nEdN" in markdown
    assert "字幕第一句" in markdown
    # 视频标题中的非法文件名字符被替换
    assert record.original_filename == "测试视频_非法_标题"


def test_ai_subtitle_is_labelled_as_ai(settings):
    record = run(
        settings,
        PlatformDownloadStub(info=make_probe_info(subtitles={"ai-zh": [{"ext": "srt", "data": SRT}]})),
    )
    assert record.status == TaskStatus.SUCCEEDED
    assert record.subtitle_kind == SubtitleKind.AI
    assert record.processing_method == ProcessingMethod.SUBTITLE_BILIBILI_AI
    markdown = Path(record.artifacts.markdown).read_text(encoding="utf-8")
    assert "字幕提取（AI 字幕）" in markdown


def test_no_subtitle_downloads_audio_then_transcribes(settings):
    transcription = FakeTranscriptionService()
    stub = PlatformDownloadStub(info=make_probe_info())
    record = run(settings, stub, transcription_service=transcription)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.extract_method == ExtractMethod.SPEECH_TO_TEXT
    assert record.processing_method == ProcessingMethod.SPEECH_TO_TEXT_REMOTE
    assert record.subtitle_kind is None
    assert record.downloaded_bytes == len(b"audio-bytes")
    assert stub.downloaded == [VIDEO_URL]
    assert len(transcription.calls) == 1
    assert (settings.audio_dir / record.task_id / "audio.wav").is_file()


def test_danmaku_only_degrades_to_transcription(settings):
    """B 站匿名访问的真实情形：只返回弹幕，必须降级而不是把弹幕当字幕。"""
    transcription = FakeTranscriptionService()
    stub = PlatformDownloadStub(
        info=make_probe_info(
            subtitles={"danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]},
            subtitle_needs_login=True,
        )
    )
    record = run(settings, stub, transcription_service=transcription)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.extract_method == ExtractMethod.SPEECH_TO_TEXT
    assert stub.downloaded == [VIDEO_URL]
    assert len(transcription.calls) == 1


def test_broken_subtitle_degrades_instead_of_failing(settings):
    transcription = FakeTranscriptionService()
    stub = PlatformDownloadStub(
        info=make_probe_info(subtitles={"zh-Hans": [{"ext": "srt", "data": "坏了"}]})
    )
    record = run(settings, stub, transcription_service=transcription)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.extract_method == ExtractMethod.SPEECH_TO_TEXT
    assert len(transcription.calls) == 1


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_stage"),
    [
        (
            AppError("MULTI_PART_NOT_SUPPORTED", "当前只支持单个视频，请粘贴某一个分集（分 P）的链接"),
            "MULTI_PART_NOT_SUPPORTED",
            TaskStatus.CHECKING_SUBTITLE,
        ),
        (
            AppError("VIDEO_TOO_LONG", "该视频时长约 4.0 小时，超过当前上限 180 分钟。建议分段处理，或改用本地文件上传"),
            "VIDEO_TOO_LONG",
            TaskStatus.CHECKING_SUBTITLE,
        ),
        (
            AppError("VIDEO_UNAVAILABLE", "无法获取该视频信息，可能是视频已删除或受限。你可以改用本地文件上传"),
            "VIDEO_UNAVAILABLE",
            TaskStatus.CHECKING_SUBTITLE,
        ),
    ],
)
def test_probe_errors_fail_task_in_checking_subtitle(
    settings, error, expected_code, expected_stage, user_copy_checker
):
    record = run(settings, PlatformDownloadStub(probe_error=error))
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == expected_code
    assert record.error.failed_stage == expected_stage
    user_copy_checker(record.error.message)


def test_download_error_fails_task_in_downloading_audio(settings, user_copy_checker):
    record = run(
        settings,
        PlatformDownloadStub(
            download_error=AppError("AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传")
        ),
    )
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "AUDIO_DOWNLOAD_FAILED"
    assert record.error.failed_stage == TaskStatus.DOWNLOADING_AUDIO
    user_copy_checker(record.error.message)


def test_vip_preview_only_fails_instead_of_pretending_success(settings, user_copy_checker):
    """大会员专享视频只能拿到预览片段，必须失败，不能用片段冒充完整逐字稿。"""
    record = run(
        settings,
        PlatformDownloadStub(
            probe_error=AppError(
                "VIP_REQUIRED",
                "该视频为大会员专享内容，当前只能获取预览片段，无法完整转写。你可以改用本地文件上传",
            )
        ),
    )
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "VIP_REQUIRED"
    user_copy_checker(record.error.message)


def test_truncated_audio_fails_instead_of_pretending_success(settings, user_copy_checker):
    """通用安全网：实际音频远短于声明时长时不能算成功。"""
    record = run(
        settings,
        PlatformDownloadStub(info=make_probe_info(duration=600.0)),
        media_service=FakeMediaService(duration_seconds=180.0),
    )
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "DOWNLOAD_INCOMPLETE"
    assert record.error.failed_stage == TaskStatus.DOWNLOADING_AUDIO
    assert "3 分钟" in record.error.message and "10 分钟" in record.error.message
    user_copy_checker(record.error.message)


def test_audio_covering_most_of_the_video_is_accepted(settings):
    record = run(
        settings,
        PlatformDownloadStub(info=make_probe_info(duration=180.0)),
        media_service=FakeMediaService(duration_seconds=178.0),
    )
    assert record.status == TaskStatus.SUCCEEDED


def test_no_speech_in_video_uses_video_wording(settings, user_copy_checker):
    """链接来源的“没有人声”提示应该针对视频，而不是文件。"""
    from backend.tests.test_processor import SilentTranscriptionService

    record = run(settings, PlatformDownloadStub(info=make_probe_info()), SilentTranscriptionService())
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "SILENT_AUDIO"
    assert "视频" in record.error.message
    assert "文件中" not in record.error.message
    user_copy_checker(record.error.message)


def test_long_video_is_not_blocked_by_duration(settings):
    """默认不限制时长：一条 5 小时视频应该照常进入下载与转写。"""
    import dataclasses

    unlimited = dataclasses.replace(settings, max_media_minutes=0)
    stub = PlatformDownloadStub(info=make_probe_info(duration=5 * 3600))
    record = run(unlimited, stub, media_service=FakeMediaService(duration_seconds=5 * 3600))
    assert record.status == TaskStatus.SUCCEEDED
    assert stub.downloaded == [VIDEO_URL]
