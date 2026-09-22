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
