from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.services.download_service import DownloadService, _YdlLogger


class StubLogger(_YdlLogger):
    pass


def make_info(**overrides) -> dict:
    info = {
        "id": "BV1BqhB6nEdN",
        "title": "测试视频",
        "duration": 174.0,
        "webpage_url": "https://www.bilibili.com/video/BV1BqhB6nEdN",
        "extractor": "BiliBili",
    }
    info.update(overrides)
    return info


class ProbeStub(DownloadService):
    """离线替身：probe 返回预设 info，下载则真实写文件（但不访问网络）。"""

    def __init__(self, *, probe_info: dict | None = None, content: bytes = b"x" * 11, **kwargs):
        super().__init__(**kwargs)
        self._probe_info = probe_info if probe_info is not None else make_info()
        self._content = content

    def _extract(self, url, *, download, options=None, fetch_subtitles=False, noplaylist=False):
        if not download:
            assert fetch_subtitles is True, "探测阶段必须显式开启字幕抓取"
            return dict(self._probe_info), StubLogger()
        target = Path(options["outtmpl"].replace(".%(ext)s", ".m4a"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._content)
        return {"requested_downloads": [{"filepath": str(target)}]}, StubLogger()


def test_probe_returns_metadata_and_subtitles():
    stub = ProbeStub(probe_info=make_info(subtitles={"zh-Hans": [{"ext": "srt", "data": "x"}]}))
    meta = stub.probe("https://www.bilibili.com/video/BV1BqhB6nEdN")
    assert meta.video_id == "BV1BqhB6nEdN"
    assert meta.duration_seconds == 174.0
    assert meta.extractor == "BiliBili"
    assert "zh-Hans" in meta.subtitles


def test_multi_part_playlist_is_rejected(user_copy_checker):
    stub = ProbeStub(probe_info={"_type": "playlist", "entries": [{"id": "a"}, {"id": "b"}]})
    with pytest.raises(AppError) as exc_info:
        stub.probe("https://www.bilibili.com/video/BV1BqhB6nEdN")
    assert exc_info.value.code == "MULTI_PART_NOT_SUPPORTED"
    user_copy_checker(exc_info.value.message)


def test_unexpected_extractor_is_rejected(user_copy_checker):
    stub = ProbeStub(probe_info=make_info(extractor="Generic"))
    with pytest.raises(AppError) as exc_info:
        stub.probe("https://www.bilibili.com/video/BV1BqhB6nEdN")
    assert exc_info.value.code == "UNSUPPORTED_PLATFORM"
    user_copy_checker(exc_info.value.message)


def test_duration_gate_blocks_before_download(user_copy_checker):
    stub = ProbeStub(probe_info=make_info(duration=4 * 3600), max_media_seconds=180 * 60)
    with pytest.raises(AppError) as exc_info:
        stub.probe("https://www.bilibili.com/video/BV1BqhB6nEdN")
    assert exc_info.value.code == "VIDEO_TOO_LONG"
    assert "4.0 小时" in exc_info.value.message
    user_copy_checker(exc_info.value.message)


def test_download_writes_audio_only_file(tmp_path: Path):
    stub = ProbeStub()
    downloaded = stub.download_audio("https://b23.tv/x", tmp_path / "dl", "task-1")
    assert downloaded.path.is_file()
    assert downloaded.size_bytes == 11


def test_download_size_gate_cleans_up(tmp_path: Path):
    stub = ProbeStub(content=b"y" * 5000, max_download_bytes=100)
    with pytest.raises(AppError) as exc_info:
        stub.download_audio("https://b23.tv/x", tmp_path / "dl", "task-1")
    assert exc_info.value.code == "DOWNLOAD_TOO_LARGE"
    assert list((tmp_path / "dl").glob("*")) == []


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("ERROR: Subtitles are only available when logged in", "LOGIN_REQUIRED"),
        ("ERROR: This video is not available", "VIDEO_UNAVAILABLE"),
        ("ERROR: Video 404 not found", "VIDEO_UNAVAILABLE"),
        ("ERROR: maximum file size exceeded", "DOWNLOAD_TOO_LARGE"),
        ("ERROR: unable to download webpage", "VIDEO_INFO_FAILED"),
    ],
)
def test_error_mapping_for_probe(text, expected_code, user_copy_checker):
    mapped = DownloadService._map_error(Exception(text), download=False)
    assert mapped.code == expected_code
    user_copy_checker(mapped.message)


def test_error_mapping_for_download_defaults_to_audio_failure():
    mapped = DownloadService._map_error(Exception("connection reset"), download=True)
    assert mapped.code == "AUDIO_DOWNLOAD_FAILED"


def test_cookie_is_written_to_a_scoped_cookie_file_and_never_logged(tmp_path: Path):
    """登录凭据必须走 cookie 文件，而不是 http_headers。

    yt-dlp 会把请求头里的 Cookie 限域到下载 URL 的主机名（.www.bilibili.com），
    而字幕接口在 api.bilibili.com，导致 Cookie 发不到字幕接口。
    """
    service = DownloadService(
        cookie="SESSDATA=secret-value; bili_jct=abc", cookie_file_dir=tmp_path / "session"
    )
    options = service._base_options()

    assert "http_headers" not in options
    cookie_file = Path(options["cookiefile"])
    assert cookie_file.is_file()
    content = cookie_file.read_text(encoding="utf-8")
    assert ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsecret-value" in content
    assert ".bilibili.com\tTRUE\t/\tTRUE\t0\tbili_jct\tabc" in content
    # 凭据文件仅当前用户可读写
    assert oct(cookie_file.stat().st_mode)[-3:] == "600"


def test_without_cookie_no_cookie_file_is_configured():
    assert "cookiefile" not in DownloadService()._base_options()
    assert "cookiefile" not in DownloadService(cookie="")._base_options()


def test_malformed_cookie_pairs_are_ignored(tmp_path: Path):
    service = DownloadService(
        cookie="SESSDATA=keepme; ; broken ;novalue=; =empty",
        cookie_file_dir=tmp_path / "session",
    )
    assert service._cookie_pairs() == [("SESSDATA", "keepme")]


def test_duration_limit_message_is_readable_in_hours(user_copy_checker):
    """6 小时上限应该显示成「6 小时」而不是「360 分钟」。"""
    from backend.app.core.messages import format_limit, video_too_long_message

    assert format_limit(360) == "6 小时"
    assert format_limit(180) == "3 小时"
    assert format_limit(90) == "90 分钟"

    message = video_too_long_message(7.5 * 3600, 360)
    assert message == "该视频时长约 7.5 小时，超过 6 小时上限。请分段处理，或改用本地文件上传"
    user_copy_checker(message)


def test_real_duration_gate_uses_six_hour_limit():
    stub = ProbeStub(probe_info=make_info(duration=7 * 3600), max_media_seconds=360 * 60,
                     max_media_minutes=360)
    with pytest.raises(AppError) as exc_info:
        stub.probe("https://www.bilibili.com/video/BV1BqhB6nEdN")
    assert exc_info.value.code == "VIDEO_TOO_LONG"
    assert "超过 6 小时上限" in exc_info.value.message

    # 5 小时播客必须放行
    ok = ProbeStub(probe_info=make_info(duration=5 * 3600), max_media_seconds=360 * 60,
                   max_media_minutes=360)
    assert ok.probe("https://www.bilibili.com/video/BV1BqhB6nEdN").duration_seconds == 5 * 3600
