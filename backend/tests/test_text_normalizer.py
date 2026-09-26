"""逐字稿规范化（繁体 → 简体 + 常见错字）。

公开前必须过这一关：面试官第一眼看到「在任何情況之下」这种繁体输出，观感会打折。
这里锁住两件事：① 规则本身正确；② **落盘前**就规范化，屏幕与下载文件一致。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.schemas.task import MediaType, TranscriptSegment
from backend.app.services.segment_store import SegmentStore
from backend.app.services.text_normalizer import (
    fix_common_errors,
    normalize_segment,
    normalize_text,
    to_simplified,
)
from backend.app.services.transcription_service import Transcription

from backend.tests.test_processor import build_processor, create_local_task


# ---------------------------------------------------------------------------
# 规则本身
# ---------------------------------------------------------------------------


def test_traditional_is_converted_to_simplified():
    assert to_simplified("在任何情況之下，神經與強迫症") == "在任何情况之下，神经与强迫症"
    assert to_simplified("顆粒、藥品、頭髮") == "颗粒、药品、头发"


def test_simplified_text_is_unchanged():
    text = "这是一段已经正确的简体中文"
    assert to_simplified(text) == text


def test_common_homophone_errors_are_fixed():
    assert fix_common_errors("腺肝的作用") == "腺苷的作用"
    assert fix_common_errors("ATP 变成 BTP") == "ATP 变成 ATP"


def test_normalize_text_handles_both_issues_together():
    assert (
        normalize_text("神經元與腺肝、BTP")
        == "神经元与腺苷、ATP"
    )


def test_normalize_text_is_idempotent():
    once = normalize_text("在任何情況之下，腺肝與 BTP")
    assert normalize_text(once) == once


def test_normalize_text_passes_through_empty_and_non_chinese():
    assert normalize_text("") == ""
    assert normalize_text("hello world 123") == "hello world 123"


def test_normalize_segment_only_copies_when_changed():
    unchanged = TranscriptSegment(start=0, end=1, text="已经正确")
    assert normalize_segment(unchanged) is unchanged

    changed = TranscriptSegment(start=0, end=1, text="神經")
    normalized = normalize_segment(changed)
    assert normalized is not changed
    assert normalized.text == "神经"
    assert (normalized.start, normalized.end) == (0, 1)


# ---------------------------------------------------------------------------
# 落盘前规范化：屏幕与下载文件必须一致
# ---------------------------------------------------------------------------


class TraditionalTranscriptionService:
    """模拟 Whisper 的真实输出：夹杂繁体与同音错字。"""

    def transcribe(self, audio_path: Path, on_segment=None, on_stage=None, **kwargs) -> Transcription:
        segment = TranscriptSegment(start=0.0, end=2.0, text="在任何情況之下，神經與腺肝")
        if on_segment is not None:
            on_segment(segment)
        return Transcription(
            text=segment.text, segments=[segment], language="zh", duration_seconds=2.0
        )


def test_asr_segments_are_normalized_before_persisting(settings):
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    store = SegmentStore(settings.outputs_dir)
    processor = build_processor(
        settings,
        transcription_service=TraditionalTranscriptionService(),
        segment_store=store,
        progress_persist_interval_seconds=0.0,
    )
    processor.process(task_id)
    processor.shutdown()

    expected = "在任何情况之下，神经与腺苷"
    # 落盘片段（界面读的就是它）
    assert [segment.text for segment in store.read_all(task_id)] == [expected]
    # 导出产物与落盘一致
    record = tasks.get(task_id)
    result = json.loads(Path(record.artifacts.result).read_text(encoding="utf-8"))
    assert result["segments"][0]["text"] == expected
    assert result["text"] == expected
