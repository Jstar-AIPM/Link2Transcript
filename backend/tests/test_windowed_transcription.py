"""阶段 3.5：长内容分窗转写（支撑 6 小时上限）。

为什么需要它：`faster-whisper` 会把整段音频一次性解码进内存，
6 小时内容约 1.38 GB，线上 2 GiB 实例会 OOM。分窗转写把音频按固定时间窗切片，
使内存占用与总时长解耦，并让中断最多只丢一个窗口。

这些测试锁住五件事：

1. 只有“时长超过窗口”的内容才分窗；短内容保持阶段 1–3 的原路径（零回归）；
2. 窗口起点/长度正确，窗口之间重叠若干秒；
3. 相邻窗口的重叠区按时间戳去重，不产生重复句子；
4. 续写时从“检查点所在窗口”开始，已完成的前面窗口不重跑；
5. 单个窗口失败/全静音时的正确处理（保留部分结果、不误判、不产半成品）。
"""

from __future__ import annotations

from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType, TaskStatus, TranscriptSegment
from backend.app.services.media_service import MediaService
from backend.app.services.segment_store import SegmentStore
from backend.app.services.transcription_service import Transcription

from backend.tests.test_processor import (
    FakeMediaService,
    build_processor,
    create_local_task,
)


class WindowMediaService(FakeMediaService):
    """记录每个窗口的 (起点, 时长)，并真的生成切片文件（供“用完即删”断言使用）。"""

    def __init__(self, duration_seconds: float | None = None) -> None:
        super().__init__(duration_seconds=duration_seconds)
        self.windows: list[tuple[float, float]] = []

    def slice_audio(
        self,
        source: Path,
        destination: Path,
        start_seconds: float,
        duration_seconds: float | None = None,
    ) -> Path:
        assert source.is_file()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")
        self.windows.append((start_seconds, duration_seconds or 0.0))
        return destination


class WindowTranscriptionService:
    """按窗口起点返回预设片段；可指定某个窗口起点抛错。"""

    def __init__(
        self,
        *,
        script: dict[float, list[TranscriptSegment]] | None = None,
        fail_at_start: float | None = None,
        duration_after_vad: float | None = None,
        window_duration: float = 1200.0,
    ) -> None:
        self.script = script or {}
        self.fail_at_start = fail_at_start
        self.duration_after_vad = duration_after_vad
        self.window_duration = window_duration
        self.calls: list[float] = []
        self.allow_empty_results: list[bool] = []

    def transcribe(
        self,
        audio_path: Path,
        on_segment=None,
        on_stage=None,
        start_offset: float = 0.0,
        allow_empty_result: bool = False,
        **kwargs,
    ) -> Transcription:
        assert audio_path.is_file()
        key = round(float(start_offset), 3)
        self.calls.append(key)
        self.allow_empty_results.append(allow_empty_result)
        if self.fail_at_start is not None and key == round(self.fail_at_start, 3):
            raise AppError("TRANSCRIPTION_FAILED", "转写未完成，请重试")
        if on_stage is not None:
            on_stage("transcribing")
        segments = list(self.script.get(key, []))
        for segment in segments:
            if on_segment is not None:
                on_segment(segment)
        return Transcription(
            text="\n".join(segment.text for segment in segments),
            segments=segments,
            language="zh",
            duration_seconds=self.window_duration,
            duration_after_vad=self.duration_after_vad,
        )


def windowed_processor(
    settings,
    *,
    duration: float,
    script=None,
    fail_at_start: float | None = None,
    duration_after_vad: float | None = None,
    window: float = 1200.0,
    overlap: float = 5.0,
    existing: list[TranscriptSegment] | None = None,
):
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    store = SegmentStore(settings.outputs_dir)
    for segment in existing or []:
        store.append(task_id, segment)
    media = WindowMediaService(duration_seconds=duration)
    service = WindowTranscriptionService(
        script=script,
        fail_at_start=fail_at_start,
        duration_after_vad=duration_after_vad,
    )
    processor = build_processor(
        settings,
        media_service=media,
        transcription_service=service,
        segment_store=store,
        progress_persist_interval_seconds=0.0,
        transcribe_window_seconds=window,
        transcribe_window_overlap_seconds=overlap,
    )
    processor.process(task_id)
    processor.shutdown()
    return tasks, task_id, store, service, media


# ---------------------------------------------------------------------------
# 分窗与重叠
# ---------------------------------------------------------------------------


def test_long_content_is_split_into_windows(settings):
    """50 分钟内容按 20 分钟窗口切成 3 段，起点分别为 0 / 1195 / 2395。"""
    tasks, task_id, store, service, media = windowed_processor(
        settings,
        duration=3000.0,
        script={
            0.0: [TranscriptSegment(start=0, end=10, text="第一窗")],
            1195.0: [TranscriptSegment(start=1195, end=1250, text="第二窗")],
            2395.0: [TranscriptSegment(start=2395, end=2500, text="第三窗")],
        },
    )

    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert service.calls == [0.0, 1195.0, 2395.0]
    assert service.allow_empty_results == [True, True, True]
    # 每个窗口都真的切片，且带上了窗口长度（-t）。
    assert [start for start, _ in media.windows] == [0.0, 1195.0, 2395.0]
    assert [length for _, length in media.windows] == [1205.0, 1210.0, 605.0]
    assert [segment.text for segment in store.read_all(task_id)] == [
        "第一窗",
        "第二窗",
        "第三窗",
    ]
    # 切片用完即删，长内容不会在磁盘上堆积几十份音频。
    assert list((settings.audio_dir / task_id).glob("window-*.wav")) == []


def test_window_overlap_is_deduplicated_by_timestamp(settings):
    """相邻窗口的重叠区只保留一次，不产生重复句子（按时间戳去重）。"""
    _, task_id, store, _, _ = windowed_processor(
        settings,
        duration=3000.0,
        script={
            0.0: [TranscriptSegment(start=1180, end=1200, text="上一窗末尾")],
            1195.0: [
                # 完全落在重叠区（end <= 检查点 1200）→ 必须丢弃
                TranscriptSegment(start=1190, end=1200, text="重叠重复"),
                TranscriptSegment(start=1200, end=1230, text="本窗内容"),
            ],
            2395.0: [TranscriptSegment(start=2400, end=2420, text="最后窗")],
        },
    )

    assert [segment.text for segment in store.read_all(task_id)] == [
        "上一窗末尾",
        "本窗内容",
        "最后窗",
    ]


# ---------------------------------------------------------------------------
# 断点续写：从检查点所在窗口开始
# ---------------------------------------------------------------------------


def test_resume_starts_from_checkpoint_window(settings):
    """检查点落在第 1 个窗口内时，第 0 个窗口不再重跑。"""
    tasks, task_id, store, service, media = windowed_processor(
        settings,
        duration=3000.0,
        existing=[
            TranscriptSegment(start=0, end=600, text="旧一"),
            TranscriptSegment(start=1150, end=1250, text="旧二"),
        ],
        script={
            1195.0: [TranscriptSegment(start=1245, end=1300, text="新一")],
            2395.0: [],
        },
    )

    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert 0.0 not in service.calls  # 已完成的窗口 0 没有重跑
    assert service.calls[0] == 1195.0
    assert [start for start, _ in media.windows][0] == 1195.0
    assert record.resumed_count == 1
    assert [segment.text for segment in store.read_all(task_id)] == ["旧一", "旧二", "新一"]


# ---------------------------------------------------------------------------
# 失败与“没有人声”
# ---------------------------------------------------------------------------


def test_window_failure_keeps_partial_result(settings):
    tasks, task_id, store, _, _ = windowed_processor(
        settings,
        duration=3000.0,
        script={0.0: [TranscriptSegment(start=0, end=100, text="第一窗")]},
        fail_at_start=1195.0,
    )

    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None and record.error.code == "TRANSCRIPTION_FAILED"
    assert record.partial_result_available is True
    assert record.segment_count == 1
    assert [segment.text for segment in store.read_all(task_id)] == ["第一窗"]
    # “成功产物才可下载”：失败时不得留下半成品文件
    assert not (settings.outputs_dir / task_id / "result.json").exists()


def test_silent_long_content_is_rejected_globally(settings):
    """所有窗口都没有人声时才判定 SILENT_AUDIO（单窗静音是正常的）。"""
    tasks, task_id, store, service, _ = windowed_processor(
        settings,
        duration=3000.0,
        script={},
        duration_after_vad=0.0,
    )

    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None and record.error.code == "SILENT_AUDIO"
    assert record.partial_result_available is False
    assert service.calls  # 确实逐窗口跑过
    assert not (settings.outputs_dir / task_id / "result.json").exists()


# ---------------------------------------------------------------------------
# 短内容零回归：分窗关闭时仍是整段一次转写
# ---------------------------------------------------------------------------


def test_windowing_disabled_uses_single_pass(settings):
    tasks, task_id, _, service, media = windowed_processor(
        settings,
        duration=3000.0,
        script={0.0: [TranscriptSegment(start=0, end=10, text="整段")]},
        window=0.0,
    )

    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert media.windows == []  # 没有切片
    assert service.calls == [0.0]
    assert service.allow_empty_results == [False]  # 非续写仍是严格模式


# ---------------------------------------------------------------------------
# MediaService：切片命令确实按窗口长度截断
# ---------------------------------------------------------------------------


def _capture_ffmpeg(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"wav")

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("backend.app.services.media_service.subprocess.run", fake_run)
    return captured


def test_slice_audio_passes_duration_to_ffmpeg(monkeypatch, tmp_path):
    service = MediaService()
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: Path("/usr/bin/ffmpeg"))
    captured = _capture_ffmpeg(monkeypatch)
    source = tmp_path / "in.m4a"
    source.write_bytes(b"audio")

    service.slice_audio(source, tmp_path / "out.wav", 1195.0, duration_seconds=1205.0)

    command = captured["command"]
    assert command[command.index("-ss") + 1] == "1195.000"
    assert command[command.index("-t") + 1] == "1205.000"


def test_slice_audio_without_duration_keeps_old_behavior(monkeypatch, tmp_path):
    service = MediaService()
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: Path("/usr/bin/ffmpeg"))
    captured = _capture_ffmpeg(monkeypatch)
    source = tmp_path / "in.m4a"
    source.write_bytes(b"audio")

    service.slice_audio(source, tmp_path / "out.wav", 3.0)

    assert "-t" not in captured["command"]
