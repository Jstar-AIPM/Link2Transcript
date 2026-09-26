"""小红书链路：Cookie 文件与选项构造（不联网）。

线上实测：云函数出口访问小红书会被判定为登录墙（yt-dlp 报 “No video formats found”），
因此与 B 站一样支持登录 Cookie；这些测试锁住「Cookie 如何落成 yt-dlp 能用的文件」，
以及「没配置 Cookie 时不产生任何文件、行为不变」。
"""

from __future__ import annotations

from pathlib import Path

from backend.app.services.xiaohongshu_service import COOKIE_DOMAIN, XiaohongshuService


def test_cookie_file_is_written_in_netscape_format(tmp_path: Path):
    service = XiaohongshuService(
        cookie="web_session=abc123; a1=def456; ; broken;=empty",
        cookie_file_dir=tmp_path,
    )
    path = service._ensure_cookie_file()
    assert path is not None and path.is_file()

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[0].startswith("# Netscape")
    body = lines[1:]
    # 只保留两条合法 cookie（broken / =empty 被忽略）
    assert len(body) == 2
    for line in body:
        parts = line.split("\t")
        assert parts[0] == COOKIE_DOMAIN  # 域必须以点开头，否则子域发不出去
        assert parts[1] == "TRUE"
        assert parts[3] == "FALSE"
    assert {"web_session", "a1"} == {line.split("\t")[5] for line in body}


def test_without_cookie_no_file_is_created(tmp_path: Path):
    service = XiaohongshuService(cookie="", cookie_file_dir=tmp_path)
    assert service._ensure_cookie_file() is None
    assert list(tmp_path.glob("*")) == []


def test_base_options_include_cookiefile_and_proxy(tmp_path: Path):
    service = XiaohongshuService(
        cookie="web_session=abc",
        cookie_file_dir=tmp_path,
        proxy="http://127.0.0.1:7890",
    )
    options = service._base_options()
    assert str(options["cookiefile"]).endswith("xiaohongshu-cookies.txt")
    assert options["proxy"] == "http://127.0.0.1:7890"
    # 必须是桌面 UA：移动到 UA 会被小红书拒绝
    assert "Macintosh" in options["http_headers"]["User-Agent"]


def test_base_options_without_cookie_have_no_cookiefile(tmp_path: Path):
    service = XiaohongshuService(cookie_file_dir=tmp_path)
    options = service._base_options()
    assert "cookiefile" not in options
    assert "proxy" not in options


def test_no_formats_without_cookie_gives_actionable_message(user_copy_checker):
    """机房 IP 拿不到视频流时，不能把技术报错丢给用户。"""
    service = XiaohongshuService()
    error = service._map_error(Exception(
        'ERROR: [XiaoHongShu] 6ab48461000000000b00692e: No video formats found!'
    ))
    assert error.code == "VIDEO_INFO_FAILED"
    assert "B 站" in error.message
    user_copy_checker(error.message)
