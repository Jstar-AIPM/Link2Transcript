from __future__ import annotations

import json
from pathlib import Path

from backend.app.schemas.task import TranscriptSegment
from backend.app.services.segment_store import SEGMENTS_FILE_NAME, SegmentStore


TASK_ID = "b6a56ee6-ba42-4b4c-b758-00766d97a59c"


def make_store(tmp_path: Path, **kwargs) -> SegmentStore:
    return SegmentStore(tmp_path / "outputs", **kwargs)


def seg(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def test_append_and_incremental_read(tmp_path: Path):
    store = make_store(tmp_path)
    assert store.append(TASK_ID, seg(0.0, 1.5, "第一句")) == 0
    assert store.append(TASK_ID, seg(1.5, 3.0, "第二句")) == 1
    assert store.append(TASK_ID, seg(3.0, 4.5, "第三句")) == 2

    assert store.count(TASK_ID) == 3
    assert [s.text for s in store.read(TASK_ID, after=0)] == ["第一句", "第二句", "第三句"]
    assert [s.text for s in store.read(TASK_ID, after=2)] == ["第三句"]
    assert [s.text for s in store.read(TASK_ID, after=3)] == []
    assert [s.index for s in store.read(TASK_ID, after=1, limit=1)] == [1]
    assert store.read(TASK_ID, after=1, limit=1)[0].text == "第二句"


def test_last_end_is_the_resume_checkpoint(tmp_path: Path):
    store = make_store(tmp_path)
    assert store.last_end(TASK_ID) == 0.0
    store.append(TASK_ID, seg(0.0, 5.0, "甲"))
    store.append(TASK_ID, seg(5.0, 12.5, "乙"))
    assert store.last_end(TASK_ID) == 12.5


def test_truncated_last_line_is_dropped_without_losing_earlier_segments(tmp_path: Path):
    """进程被强杀时最后一行可能是半截 JSON，不能因此丢掉全部片段。"""
    store = make_store(tmp_path)
    store.append(TASK_ID, seg(0.0, 1.0, "完整一"))
    store.append(TASK_ID, seg(1.0, 2.0, "完整二"))
    path = store.path_for(TASK_ID)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"index": 2, "start": 2.0, "end": 3.0, "tex')

    assert store.count(TASK_ID) == 2
    assert [s.text for s in store.read_all(TASK_ID)] == ["完整一", "完整二"]
    # 断点也取不到被截断的那一行
    assert store.last_end(TASK_ID) == 2.0
    # 并且可以继续追加，序号接在有效片段之后
    assert store.append(TASK_ID, seg(3.0, 4.0, "续写")) == 2


def test_unicode_and_timestamps_round_trip(tmp_path: Path):
    store = make_store(tmp_path)
    store.append(TASK_ID, seg(0.4, 5.45, "毕业之后呢，进了一个电池厂"))
    stored = store.read_all(TASK_ID)[0]
    assert stored.text == "毕业之后呢，进了一个电池厂"
    assert stored.start == 0.4 and stored.end == 5.45
    # 磁盘上是可读的 UTF-8，不是转义序列
    raw = store.path_for(TASK_ID).read_text(encoding="utf-8")
    assert "毕业之后呢" in raw
    assert json.loads(raw.strip())["index"] == 0


def test_negative_timestamps_are_blocked_at_the_schema_layer(tmp_path: Path):
    """负时间戳在片段模型层就被拒绝，不会写进存储。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        seg(-5.0, 3.0, "异常时间")

    # 存储层仍保留兵底夹紧，防止绕过模型直接传入的对象把脏数据写下去。
    class Duck:
        start = -5.0
        end = 3.0
        text = "异常时间"

    store = make_store(tmp_path)
    store.append(TASK_ID, Duck())
    stored = store.read_all(TASK_ID)[0]
    assert stored.start == 0.0 and stored.end == 3.0


def test_segment_cap_stops_collecting_and_warns_once(tmp_path: Path, caplog):
    store = make_store(tmp_path, max_segments=3)
    for i in range(3):
        assert store.append(TASK_ID, seg(float(i), float(i) + 1, f"片段{i}")) == i
    assert store.append(TASK_ID, seg(9.0, 10.0, "超出")) is None
    assert store.append(TASK_ID, seg(10.0, 11.0, "再超出")) is None
    assert store.count(TASK_ID) == 3
    assert sum("segment_limit_reached" in r.message for r in caplog.records) == 1


def test_missing_task_reads_are_empty_not_errors(tmp_path: Path):
    store = make_store(tmp_path)
    assert store.read_all("07ba2677-e78f-4aa5-939f-aa41bb4cf11a") == []
    assert store.count("07ba2677-e78f-4aa5-939f-aa41bb4cf11a") == 0
    assert store.last_end("07ba2677-e78f-4aa5-939f-aa41bb4cf11a") == 0.0
    assert store.has_content("07ba2677-e78f-4aa5-939f-aa41bb4cf11a") is False


def test_reset_clears_the_task_file(tmp_path: Path):
    store = make_store(tmp_path)
    store.append(TASK_ID, seg(0.0, 1.0, "甲"))
    assert store.has_content(TASK_ID) is True
    store.reset(TASK_ID)
    assert store.has_content(TASK_ID) is False
    assert store.path_for(TASK_ID).name == SEGMENTS_FILE_NAME
