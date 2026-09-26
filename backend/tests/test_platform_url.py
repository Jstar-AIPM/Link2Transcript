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


def test_default_registry_enables_bilibili_and_xiaohongshu(user_copy_checker):
    """默认启用 B 站与小红书；抖音尚未启用（给专门的中文提示）。"""
    service = PlatformService()
    assert service.supported_names() == ["B站", "小红书"]
    assert "小红书" in service.unsupported_message()
    user_copy_checker(service.unsupported_message())

    with pytest.raises(AppError) as exc_info:
        service.resolve("https://www.douyin.com/video/123456")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"
    assert "抖音" in exc_info.value.message
    user_copy_checker(exc_info.value.message)

    with pytest.raises(AppError) as exc_info:
        service.resolve("https://www.youtube.com/watch?v=abcdefg")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"


XHS_NOTE_ID = "6ab48461000000000b00692e"


def test_xiaohongshu_urls_are_accepted():
    service = PlatformService()
    for url in (
        f"https://www.xiaohongshu.com/explore/{XHS_NOTE_ID}",
        f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}?xsec_token=ABC%3D",
    ):
        resolved = service.resolve(url)
        assert resolved.platform == Platform.XIAOHONGSHU
        assert resolved.video_id == XHS_NOTE_ID
    # xsec_token 在查询串里，必须原样保留（丢掉会导致后续解析失败）
    with_token = service.resolve(
        f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}?xsec_token=ABC%3D"
    )
    assert "xsec_token=ABC%3D" in with_token.url


def test_xiaohongshu_short_link_resolves_to_real_note():
    item = f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}?xsec_token=ABC%3D"
    service = PlatformService(short_link_resolver=lambda url: item)
    resolved = service.resolve("https://xhslink.cn/o/437QhrQY86l")
    assert resolved.platform == Platform.XIAOHONGSHU
    assert resolved.video_id == XHS_NOTE_ID
    assert resolved.url == item


def test_xhs_short_link_pointing_elsewhere_is_rejected():
    service = PlatformService(
        short_link_resolver=lambda url: "https://www.bilibili.com/video/BV1BqhB6nEdN"
    )
    with pytest.raises(AppError) as exc_info:
        service.resolve("https://xhslink.cn/o/abc")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"


def test_xiaohongshu_pasted_share_text_is_extracted():
    text = (
        "在任何情况之下，向外证明自己过得比别人好，都是一件... "
        f"https://xhslink.cn/o/437QhrQY86l 复制一下，打开【小红书】就能阅读全文。"
    )
    service = PlatformService(short_link_resolver=lambda url: f"https://www.xiaohongshu.com/discovery/item/{XHS_NOTE_ID}")
    resolved = service.resolve(text)
    assert resolved.platform == Platform.XIAOHONGSHU
    assert resolved.video_id == XHS_NOTE_ID


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


# ---------------------------------------------------------------------------
# 粘贴整段分享文案：自动提取真实链接
# ---------------------------------------------------------------------------


def test_share_text_with_noise_extracts_url():
    """用户在 App 里点“复制链接”，拿到的是标题 + 链接 + 口令的一大段文字。"""
    text = (
        "3.30 复制打开抖音，看看【朴素之道的作品】如何练好字，学好英语口语 "
        "https://www.bilibili.com/video/BV1BqhB6nEdN?p=1 X@M.Wz :7pm kpD:/ 03/17"
    )
    resolved = make_service().resolve(text)
    assert resolved.platform == Platform.BILIBILI
    assert resolved.video_id == "BV1BqhB6nEdN"
    # 存的是提取出来的链接，而不是整段文案
    assert resolved.original_url == "https://www.bilibili.com/video/BV1BqhB6nEdN?p=1"


def test_share_text_url_with_trailing_punctuation_is_trimmed():
    resolved = make_service().resolve("看这个视频 https://www.bilibili.com/video/BV1BqhB6nEdN。")
    assert resolved.video_id == "BV1BqhB6nEdN"


def test_share_text_works_for_unsupported_platform_too():
    """能提取出链接（即使该平台尚未启用，也应是 UNSUPPORTED_PLATFORM 而不是“没有链接”）。"""
    text = "9.41 复制打开抖音 https://v.douyin.com/LnXEkdYvgMM/ w@S.LW GiP:/ 03/23"
    with pytest.raises(AppError) as exc_info:
        PlatformService().resolve(text)
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"


def test_share_text_without_any_link_is_rejected(user_copy_checker):
    with pytest.raises(AppError) as exc_info:
        make_service().resolve("今天分享一个视频，忘了贴链接")
    assert exc_info.value.code == "INVALID_SOURCE_URL"
    user_copy_checker(exc_info.value.message)
