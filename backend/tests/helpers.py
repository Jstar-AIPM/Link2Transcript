from __future__ import annotations

import re

import pytest


# 用户可见文案里不允许出现的技术痕迹。
FORBIDDEN_TOKENS = (
    "traceback",
    "exception",
    "error:",
    "yt-dlp",
    "yt_dlp",
    "sessdata",
    "stack",
    "none",
    "http://",
    "https://",
    "json",
)

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def assert_user_copy(message: str) -> None:
    """断言这是给用户看的中文文案，而不是程序报错。"""
    assert isinstance(message, str) and message.strip(), "用户文案不能为空"
    lowered = message.lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lowered, f"用户文案包含技术术语 {token!r}: {message}"
    assert CJK_PATTERN.search(message), f"用户文案必须是中文: {message}"


@pytest.fixture
def user_copy_checker():
    return assert_user_copy
