from __future__ import annotations

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import Platform
from backend.app.services.platform_service import BilibiliAdapter, PlatformService


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


# ---------------------------------------------------------------------------
# 阶段 5：适配器注册表（新增平台不改主链路）
# ---------------------------------------------------------------------------


def test_default_registry_only_enables_bilibili(user_copy_checker):
    """抖音 / 小红书未完成真实网络验证前不得被放行（否则会创建必然失败的任务）。"""
    service = PlatformService()
    assert service.supported_names() == ["B站"]
    assert "B站" in service.unsupported_message()
    user_copy_checker(service.unsupported_message())

    for url in ("https://www.douyin.com/video/123456", "https://www.xiaohongshu.com/explore/123"):
        with pytest.raises(AppError) as exc_info:
            service.resolve(url)
        assert exc_info.value.code == "UNSUPPORTED_PLATFORM"


def test_registry_lets_a_new_platform_plug_in():
    """注册一个适配器即可支持新平台（含短链解析与平台码）。"""
    import re as _re

    from backend.app.services.platform_service import RegexPathAdapter

    douyin = RegexPathAdapter(
        platform=Platform.DOUYIN,
        name="抖音",
        domains=frozenset({"douyin.com"}),
        short_link_domains=frozenset({"v.douyin.com"}),
        path_pattern=_re.compile(r"^/video/(?P<video_id>\d+)/?$"),
    )
    service = PlatformService(
        short_link_resolver=lambda url: "https://www.douyin.com/video/123456",
        adapters=[BilibiliAdapter, douyin],
    )

    resolved = service.resolve("https://v.douyin.com/abcdEF/")
    assert resolved.platform == Platform.DOUYIN
    assert resolved.video_id == "123456"
    assert resolved.url == "https://www.douyin.com/video/123456"
    assert service.supported_names() == ["B站", "抖音"]
    assert "抖音" in service.unsupported_message()


def test_short_link_cannot_switch_platform():
    """短链只能跳到同平台正式域名，不能“跳到别的平台”绕过白名单。"""
    import re as _re

    from backend.app.services.platform_service import RegexPathAdapter

    douyin = RegexPathAdapter(
        platform=Platform.DOUYIN,
        name="抖音",
        domains=frozenset({"douyin.com"}),
        short_link_domains=frozenset({"v.douyin.com"}),
        path_pattern=_re.compile(r"^/video/(?P<video_id>\d+)/?$"),
    )
    # 抖音短链却跳到了 B 站：必须拒绝
    service = PlatformService(
        short_link_resolver=lambda url: "https://www.bilibili.com/video/BV1BqhB6nEdN",
        adapters=[BilibiliAdapter, douyin],
    )
    with pytest.raises(AppError) as exc_info:
        service.resolve("https://v.douyin.com/abcdEF/")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"
