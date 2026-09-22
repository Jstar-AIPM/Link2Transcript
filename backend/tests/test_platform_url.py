from __future__ import annotations

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import Platform
from backend.app.services.platform_service import PlatformService


def make_service(final_url: str = "") -> PlatformService:
    return PlatformService(
        timeout_seconds=5,
        short_link_resolver=lambda url: final_url or "https://www.bilibili.com/video/BV1BqhB6nEdN",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/BV1BqhB6nEdN",
        "https://www.bilibili.com/video/BV1BqhB6nEdN/",
        "https://www.bilibili.com/video/av123456",
        "https://m.bilibili.com/video/BV1BqhB6nEdN",
        "https://www.bilibili.com/video/BV1BqhB6nEdN?p=2&t=30",
        "http://www.bilibili.com/video/BV1BqhB6nEdN",
        "  https://www.bilibili.com/video/BV1BqhB6nEdN  ",
    ],
)
def test_valid_bilibili_video_urls_are_accepted(url, user_copy_checker):
    resolved = make_service().resolve(url)
    assert resolved.platform == Platform.BILIBILI
    assert resolved.video_id.startswith("BV") or resolved.video_id.startswith("av")
    assert resolved.original_url == url.strip()


def test_short_link_is_resolved_and_revalidated():
    service = make_service("https://www.bilibili.com/video/BV1BqhB6nEdN")
    resolved = service.resolve("https://b23.tv/abcdefg")
    assert resolved.url == "https://www.bilibili.com/video/BV1BqhB6nEdN"
    assert resolved.original_url == "https://b23.tv/abcdefg"


def test_short_link_pointing_elsewhere_is_rejected(user_copy_checker):
    service = make_service("https://evil.example.com/video/BV1BqhB6nEdN")
    with pytest.raises(AppError) as exc_info:
        service.resolve("https://b23.tv/abcdefg")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"
    user_copy_checker(exc_info.value.message)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.douyin.com/video/123456",
        "https://www.xiaohongshu.com/explore/123456",
        "https://www.youtube.com/watch?v=abcdefg",
        "https://www.bilibili.com.evil.com/video/BV1BqhB6nEdN",
    ],
)
def test_non_bilibili_platforms_are_rejected(url, user_copy_checker):
    with pytest.raises(AppError) as exc_info:
        make_service().resolve(url)
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"
    user_copy_checker(exc_info.value.message)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://www.bilibili.com/",
        "https://www.bilibili.com/video/",
        "https://www.bilibili.com/bangumi/play/ep123456",
        "https://www.bilibili.com/video/BV123",
        "ftp://www.bilibili.com/video/BV1BqhB6nEdN",
        "https://www.bilibili.com/video/BV1BqhB6nEdN" + "x" * 2100,
    ],
)
def test_invalid_urls_are_rejected(url, user_copy_checker):
    with pytest.raises(AppError) as exc_info:
        make_service().resolve(url)
    assert exc_info.value.code == "INVALID_SOURCE_URL"
    user_copy_checker(exc_info.value.message)
