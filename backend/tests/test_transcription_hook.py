from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.services.transcription_service import TranscriptionService


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeModel:
    def transcribe(self, path: str, **kwargs):
        segments = [_FakeSegment(0, 1, "第一句"), _FakeSegment(1, 2, "   "), _FakeSegment(2, 3, "第二句")]
        info = SimpleNamespace(duration=3.0, language="zh")
        return iter(segments), info


def test_transcribe_supports_progressive_segment_callback():
    """阶段 3 的接口口子：不传回调时行为不变，传回调时能随解码过程拿到片段。"""
    service = TranscriptionService("tiny", "cpu", "int8")
    service._model = _FakeModel()

    seen = []
    result = service.transcribe(Path("unused.wav"), on_segment=seen.append)

    assert [segment.text for segment in seen] == ["第一句", "第二句"]
    assert [segment.text for segment in result.segments] == ["第一句", "第二句"]
    assert result.text == "第一句\n第二句"
    assert result.language == "zh"
    assert result.duration_seconds == 3.0


def test_transcribe_without_callback_still_returns_full_result():
    service = TranscriptionService("tiny", "cpu", "int8")
    service._model = _FakeModel()
    result = service.transcribe(Path("unused.wav"))
    assert result.text == "第一句\n第二句"


def _install_fake_model(service: TranscriptionService, model, *, mark_loaded: bool = True):
    """绕过真实模型加载（含磁盘缓存），同时能模拟“冷启动 / 已加载”两种情况。"""

    def load():
        if mark_loaded:
            service._model = model
        return model

    service._get_model = load


def test_stage_callback_reports_model_loading_then_transcribing():
    """首字延迟（模型加载、开始解码）必须能被上层感知，否则页面看起来像卡死。"""
    service = TranscriptionService("tiny", "cpu", "int8")
    _install_fake_model(service, _FakeModel())

    stages: list[str] = []
    service.transcribe(Path("unused.wav"), on_stage=stages.append)
    assert stages == ["loading_model", "transcribing"]

    # 同一进程内的第二个任务：模型已加载，不再重复报“正在加载模型”
    stages.clear()
    service.transcribe(Path("unused.wav"), on_stage=stages.append)
    assert stages == ["transcribing"]


def test_start_offset_shifts_segment_timestamps():
    """断点续写：续写音频的片段时间戳要能拼回全局时间轴。"""
    service = TranscriptionService("tiny", "cpu", "int8")
    _install_fake_model(service, _FakeModel())

    seen = []
    result = service.transcribe(Path("unused.wav"), on_segment=seen.append, start_offset=100.0)

    assert [(s.start, s.end) for s in result.segments] == [(100.0, 101.0), (102.0, 103.0)]
    assert [(s.start, s.end) for s in seen] == [(100.0, 101.0), (102.0, 103.0)]


def test_negative_start_offset_is_clamped():
    service = TranscriptionService("tiny", "cpu", "int8")
    _install_fake_model(service, _FakeModel())
    result = service.transcribe(Path("unused.wav"), start_offset=-5.0)
    assert result.segments[0].start == 0.0


# ---------------------------------------------------------------------------
# 没有人声的内容：必须明确报错，不能静默产出垃圾文本
# ---------------------------------------------------------------------------

from types import SimpleNamespace as _NS

import pytest as _pytest

from backend.app.core.errors import AppError as _AppError


def _service_with_info(info) -> TranscriptionService:
    class _Model:
        def transcribe(self, path, **kwargs):
            return iter([_FakeSegment(0, 1, "误识别文本")]), info

    service = TranscriptionService("tiny", "cpu", "int8")
    service._model = _Model()
    return service


@_pytest.mark.parametrize(
    ("duration", "after_vad", "expected_code"),
    [
        (122.069, 2.176, "SILENT_AUDIO"),   # 实测：以音乐为主的视频，语音仅占 1.8%
        (300.0, 0.0, "SILENT_AUDIO"),        # 完全静音
        (300.0, 0.4, "SILENT_AUDIO"),        # 有效语音不足 0.5 秒
        (300.0, 240.0, None),                # 正常语音，不应报错
        (300.0, 20.0, None),                 # 人声稀疏但不低于阈值，不应误伤
    ],
)
def test_silent_content_is_rejected_before_decoding(duration, after_vad, expected_code):
    info = _NS(duration=duration, duration_after_vad=after_vad, language="zh")
    service = _service_with_info(info)
    if expected_code is None:
        assert service.transcribe(Path("unused.wav")).text == "误识别文本"
        return
    with _pytest.raises(_AppError) as exc_info:
        service.transcribe(Path("unused.wav"))
    assert exc_info.value.code == expected_code


def test_missing_vad_info_does_not_block_normal_content():
    info = _NS(duration=300.0, language="zh")  # 没有 duration_after_vad
    assert _service_with_info(info).transcribe(Path("unused.wav")).text == "误识别文本"


def test_empty_text_raises_empty_transcript():
    class _Model:
        def transcribe(self, path, **kwargs):
            return iter([_FakeSegment(0, 1, "   ")]), _NS(duration=10.0, duration_after_vad=9.0)

    service = TranscriptionService("tiny", "cpu", "int8")
    service._model = _Model()
    with _pytest.raises(_AppError) as exc_info:
        service.transcribe(Path("unused.wav"))
    assert exc_info.value.code == "EMPTY_TRANSCRIPT"


def test_resume_mode_tolerates_silent_or_empty_tail():
    """续写片段的尾部可能全是静音："没有新片段"不是失败（阶段 3B）。"""
    info = _NS(duration=10.0, duration_after_vad=0.0, language="zh")

    class _Model:
        def transcribe(self, path, **kwargs):
            return iter([]), info

    service = TranscriptionService("tiny", "cpu", "int8")
    service._model = _Model()

    result = service.transcribe(Path("unused.wav"), allow_empty_result=True)
    assert result.segments == [] and result.text == ""

    # 同样的输入，非续写模式仍按严格行为报错
    with _pytest.raises(_AppError) as exc_info:
        service.transcribe(Path("unused.wav"))
    assert exc_info.value.code == "SILENT_AUDIO"
