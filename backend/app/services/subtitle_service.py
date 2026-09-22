from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from backend.app.schemas.task import SubtitleKind, TranscriptSegment


logger = logging.getLogger(__name__)

# B 站把弹幕也放在 subtitles 里，它不是字幕，必须排除。
DANMAKU_LANG = "danmaku"
# B 站 AI 字幕的语言代码形如 ai-zh；人工 CC 字幕形如 zh-Hans / zh-CN。
AI_LANG_PREFIX = "ai-"

PREFERRED_LANGUAGES = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "ai-zh")

TIME_RANGE_RE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sfrac>\d{1,3})"
    r"\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<efrac>\d{1,3})"
)


@dataclass(frozen=True)
class SubtitleSelection:
    kind: SubtitleKind
    language: str
    segments: list[TranscriptSegment]

    @property
    def text(self) -> str:
        return "\n".join(segment.text for segment in self.segments).strip()


def _to_seconds(hours: str, minutes: str, seconds: str, fraction: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def parse_srt(payload: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for block in re.split(r"\r?\n\s*\r?\n", payload.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_index = next(
            (index for index, line in enumerate(lines) if TIME_RANGE_RE.search(line)), None
        )
        if time_index is None:
            continue
        match = TIME_RANGE_RE.search(lines[time_index])
        if match is None:
            continue
        text = " ".join(lines[time_index + 1 :]).strip()
        if not text:
            continue
        start = _to_seconds(
            match.group("sh"), match.group("sm"), match.group("ss"), match.group("sfrac")
        )
        end = _to_seconds(
            match.group("eh"), match.group("em"), match.group("es"), match.group("efrac")
        )
        segments.append(
            TranscriptSegment(start=max(0.0, start), end=max(0.0, end), text=text)
        )
    return segments


def parse_bilibili_json(payload: str) -> list[TranscriptSegment]:
    """B 站字幕原始结构：{"body": [{"from": 0.0, "to": 1.5, "content": "..."}]}"""
    data = json.loads(payload)
    body = data.get("body") if isinstance(data, dict) else None
    if not isinstance(body, list):
        return []
    segments: list[TranscriptSegment] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("from") or 0.0)
            end = float(item.get("to") or 0.0)
        except (TypeError, ValueError):
            continue
        segments.append(
            TranscriptSegment(start=max(0.0, start), end=max(0.0, end), text=text)
        )
    return segments


def normalize_segments(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """去空、纠正时间倒挂、合并紧邻的重复文本（B 站字幕偶有回显）。"""
    cleaned: list[TranscriptSegment] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        start = max(0.0, segment.start)
        end = max(start, segment.end)
        if cleaned and cleaned[-1].text == text:
            previous = cleaned[-1]
            cleaned[-1] = TranscriptSegment(
                start=previous.start, end=max(previous.end, end), text=previous.text
            )
            continue
        cleaned.append(TranscriptSegment(start=start, end=end, text=text))
    return cleaned


class SubtitleService:
    """字幕选择与标准化。

    优先级：人工字幕（CC） > AI 字幕 > 语音转写。
    任何一步取不到或解析失败都返回 ``None``，由调用方降级为语音转写，**不算任务失败**。
    """

    def select(self, subtitles: dict, automatic_captions: dict) -> SubtitleSelection | None:
        manual = self._pick(subtitles, want_ai=False)
        if manual is not None:
            return SubtitleSelection(
                kind=SubtitleKind.CC, language=manual[0], segments=manual[1]
            )
        ai_from_subtitles = self._pick(subtitles, want_ai=True)
        if ai_from_subtitles is not None:
            return SubtitleSelection(
                kind=SubtitleKind.AI, language=ai_from_subtitles[0], segments=ai_from_subtitles[1]
            )
        ai_from_automatic = self._pick(automatic_captions, want_ai=True, require_ai=False)
        if ai_from_automatic is not None:
            return SubtitleSelection(
                kind=SubtitleKind.AI,
                language=ai_from_automatic[0],
                segments=ai_from_automatic[1],
            )
        return None

    # ------------------------------------------------------------------ 内部

    def _pick(
        self, group: dict, *, want_ai: bool, require_ai: bool = True
    ) -> tuple[str, list[TranscriptSegment]] | None:
        if not isinstance(group, dict):
            return None
        candidates: list[str] = []
        for language in group:
            if language == DANMAKU_LANG:
                continue
            is_ai = str(language).lower().startswith(AI_LANG_PREFIX)
            if require_ai and is_ai != want_ai:
                continue
            candidates.append(language)
        if not candidates:
            return None
        for language in self._sort_languages(candidates):
            segments = self._segments_for(group.get(language))
            if segments:
                return language, segments
            logger.info("subtitle_parse_failed language=%s", language)
        return None

    @staticmethod
    def _sort_languages(languages: list[str]) -> list[str]:
        def rank(language: str) -> tuple[int, str]:
            lowered = language.lower()
            for index, preferred in enumerate(PREFERRED_LANGUAGES):
                if lowered == preferred.lower():
                    return (index, lowered)
            return (len(PREFERRED_LANGUAGES), lowered)

        return sorted(languages, key=rank)

    def _segments_for(self, entries: object) -> list[TranscriptSegment]:
        if not isinstance(entries, list):
            return []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            payload = entry.get("data")
            if not isinstance(payload, str) or not payload.strip():
                # yt-dlp 只对已下载的字幕给出 data；仅带 url 的条目不在这里处理。
                continue
            ext = str(entry.get("ext") or "").lower()
            segments: list[TranscriptSegment] = []
            if ext == "json" or payload.lstrip().startswith("{"):
                try:
                    segments = parse_bilibili_json(payload)
                except (ValueError, TypeError):
                    segments = []
            if not segments:
                segments = parse_srt(payload)
            normalized = normalize_segments(segments)
            if normalized:
                return normalized
        return []
