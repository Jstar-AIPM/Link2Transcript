from __future__ import annotations

import re


_UNSAFE_PATTERN = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WHITESPACE_PATTERN = re.compile(r"\s+")
MAX_STEM_LENGTH = 120


def sanitize_filename(raw: str | None, *, fallback: str, max_length: int = MAX_STEM_LENGTH) -> str:
    """把任意文本转成可安全用于文件名的字符串（不改变扩展名语义）。

    用于链接类任务：视频标题可能包含路径分隔符、控制字符或超长内容。
    """
    candidate = _UNSAFE_PATTERN.sub("_", str(raw or ""))
    candidate = _WHITESPACE_PATTERN.sub(" ", candidate).strip(" ._")
    if len(candidate) > max_length:
        candidate = candidate[:max_length].strip(" ._")
    return candidate or fallback
