from __future__ import annotations

from backend.app.schemas.task import SubtitleKind
from backend.app.services.subtitle_service import (
    SubtitleService,
    normalize_segments,
    parse_bilibili_json,
    parse_srt,
)
from backend.app.schemas.task import TranscriptSegment


SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:01,500\n"
    "第一句\n"
    "\n"
    "2\n"
    "00:00:01,500 --> 00:00:03,250\n"
    "第二句\n"
)


def test_parse_srt_handles_comma_and_dot_separators():
    segments = parse_srt(SRT)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 1.5, "第一句"),
        (1.5, 3.25, "第二句"),
    ]
    assert parse_srt(SRT.replace(",", ".")) == segments


def test_parse_bilibili_json_maps_from_to_content():
    payload = '{"body": [{"from": 0.0, "to": 1.2, "content": "甲"}, {"from": 1.2, "to": 2.0, "content": "  "}]}'
    segments = parse_bilibili_json(payload)
    assert [(s.start, s.end, s.text) for s in segments] == [(0.0, 1.2, "甲")]


def test_normalize_merges_consecutive_duplicates_and_fixes_inverted_time():
    segments = normalize_segments(
        [
            TranscriptSegment(start=0.0, end=1.0, text="重复"),
            TranscriptSegment(start=1.0, end=2.0, text="重复"),
            TranscriptSegment(start=5.0, end=3.0, text="倒挂"),
            TranscriptSegment(start=6.0, end=7.0, text="   "),
        ]
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 2.0, "重复"),
        (5.0, 5.0, "倒挂"),
    ]


def test_manual_subtitle_wins_over_ai():
    service = SubtitleService()
    selection = service.select(
        {
            "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}],
            "ai-zh": [{"ext": "srt", "data": SRT.replace("第一句", "AI 第一句")}],
            "zh-Hans": [{"ext": "srt", "data": SRT}],
        },
        {},
    )
    assert selection is not None
    assert selection.kind == SubtitleKind.CC
    assert selection.language == "zh-Hans"
    assert selection.text.splitlines()[0] == "第一句"


def test_ai_subtitle_used_when_no_manual_subtitle():
    service = SubtitleService()
    selection = service.select(
        {"danmaku": [{"ext": "xml", "url": "x"}], "ai-zh": [{"ext": "srt", "data": SRT}]},
        {},
    )
    assert selection is not None
    assert selection.kind == SubtitleKind.AI
    assert selection.language == "ai-zh"


def test_danmaku_alone_is_not_treated_as_subtitle():
    service = SubtitleService()
    assert service.select(
        {"danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]}, {}
    ) is None


def test_unparsable_subtitle_returns_none_so_caller_can_degrade():
    service = SubtitleService()
    assert service.select({"zh-Hans": [{"ext": "srt", "data": "不是字幕内容"}]}, {}) is None
    assert service.select({"zh-Hans": [{"ext": "json", "url": "https://example.com/a.json"}]}, {}) is None


def test_automatic_captions_used_as_ai_fallback():
    service = SubtitleService()
    selection = service.select({}, {"zh-Hans": [{"ext": "srt", "data": SRT}]})
    assert selection is not None
    assert selection.kind == SubtitleKind.AI
