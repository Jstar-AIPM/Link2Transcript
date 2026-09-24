"""B 站 API 链路（阶段 6）的离线测试。

为什么不抓 HTML：B 站对机房 IP 的 HTML 页面返回 412，而 api.bilibili.com 可用。
这里用假的会话对象验证解析、校验与降级逻辑，全部离线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.services.bilibili_api_service import BilibiliApiService, MIXIN_KEY_ENC_TAB


class FakeSession:
    """按 URL 关键词返回预置响应，并记录调用次数。"""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.downloads: list[str] = []

    def get_json(self, url, params=None, timeout=20):
        self.calls.append(url)
        for key, payload in self.responses.items():
            if key in url:
                if isinstance(payload, list):  # 依次返回（模拟字幕逐渐生成）
                    return payload.pop(0) if payload else {}
                return payload
        raise OSError(f"no fake response for {url}")

    def get_text(self, url, timeout=20):
        self.calls.append(url)
        return str(self.responses.get("subtitle_body", ""))

    def download(self, url, destination: Path, timeout=300):
        self.downloads.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x" * 1024)
        return 1024


def make_service(responses: dict, **kwargs) -> BilibiliApiService:
    service = BilibiliApiService(max_media_seconds=3600, max_media_minutes=60, **kwargs)
    service.session = FakeSession(responses)
    return service


VIEW = {
    "code": 0,
    "data": {
        "aid": 111,
        "cid": 222,
        "title": "测试视频",
        "duration": 600,
        "pages": [{"cid": 222, "duration": 600}],
    },
}


def subtitle_payload(coverage: float) -> str:
    return json.dumps({"body": [{"from": 0.0, "to": coverage, "content": "内容"}]})


def player_with(lan: str, coverage: float = 590.0) -> dict:
    return {
        "code": 0,
        "data": {
            "subtitle": {
                "subtitles": [
                    {"lan": lan, "lan_doc": lan, "subtitle_url": "//sub.example/x.json"}
                ]
            }
        },
    }


def test_probe_maps_ai_subtitle_and_manual_correctly():
    service = make_service(
        {
            "web-interface/view": VIEW,
            "player/v2": player_with("ai-zh"),
            "subtitle_body": subtitle_payload(590.0),
        }
    )
    meta = service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert meta.extractor == "BiliBiliApi"
    assert meta.duration_seconds == 600
    assert list(meta.subtitles) == ["ai-zh"]
    # 交给 SubtitleService 的形状与 yt-dlp 一致（ext + data）
    assert meta.subtitles["ai-zh"][0]["ext"] == "json"


def test_incomplete_subtitle_is_refused_so_callers_fall_back():
    """AI 字幕只覆盖前半段时必须丢弃：宁可降级转写，也不要半截逐字稿。"""
    service = make_service(
        {
            "web-interface/view": VIEW,
            "player/v2": player_with("ai-zh"),
            "subtitle_body": subtitle_payload(150.0),  # 覆盖 25%
        }
    )
    meta = service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert meta.subtitles == {}


def test_incomplete_then_complete_subtitle_is_accepted():
    """第一次返回不完整、第二次完整时，应当采用完整的这次。"""
    service = make_service(
        {
            "web-interface/view": VIEW,
            "player/v2": player_with("zh-CN"),
            "subtitle_body": subtitle_payload(590.0),
        }
    )
    bodies = [subtitle_payload(100.0), subtitle_payload(590.0)]
    service.session.responses["subtitle_body"] = bodies[0]

    original_get_text = service.session.get_text
    state = {"i": 0}

    def sequential(url, timeout=20):
        payload = bodies[min(state["i"], len(bodies) - 1)]
        state["i"] += 1
        return payload

    service.session.get_text = sequential  # type: ignore[assignment]
    meta = service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert list(meta.subtitles) == ["zh-CN"]
    assert state["i"] == 2


def test_multi_part_link_without_page_is_rejected():
    view = json.loads(json.dumps(VIEW))
    view["data"]["pages"] = [{"cid": 1, "duration": 10}, {"cid": 2, "duration": 10}]
    service = make_service({"web-interface/view": view, "player/v2": player_with("ai-zh")})
    with pytest.raises(AppError) as exc_info:
        service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert exc_info.value.code == "MULTI_PART_NOT_SUPPORTED"


def test_single_page_with_out_of_range_p_param_is_rejected():
    service = make_service({"web-interface/view": VIEW, "player/v2": player_with("ai-zh")})
    with pytest.raises(AppError) as exc_info:
        service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR?p=99")
    assert exc_info.value.code == "INVALID_SOURCE_URL"


def test_duration_gate_runs_before_download():
    service = BilibiliApiService(max_media_seconds=300, max_media_minutes=5)
    service.session = FakeSession({"web-interface/view": VIEW, "player/v2": player_with("ai-zh")})
    with pytest.raises(AppError) as exc_info:
        service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert exc_info.value.code == "VIDEO_TOO_LONG"
    assert "超过 5 分钟上限" in exc_info.value.message


def test_audio_download_picks_smallest_stream_and_signs_request():
    playurl = {
        "code": 0,
        "data": {
            "dash": {
                "audio": [
                    {"baseUrl": "https://cdn/high.m4s", "bandwidth": 192000, "size": 900},
                    {"baseUrl": "https://cdn/low.m4s", "bandwidth": 64000, "size": 300},
                ]
            }
        },
    }
    service = make_service(
        {"web-interface/view": VIEW, "player/v2": player_with("ai-zh"), "playurl": playurl}
    )
    # WBI 密钥直接注入，避免依赖 nav 接口
    service._wbi_keys = ("a" * 32, "b" * 32)

    downloaded = service.download_audio(
        "https://www.bilibili.com/video/BV1VVhk6pEiR", Path("/tmp/x"), "t1"
    )
    assert downloaded.size_bytes == 1024
    # 选了带宽最小的那一路（更快、更省流量）
    assert service.session.downloads == ["https://cdn/low.m4s"]


def test_wbi_signature_shape():
    service = BilibiliApiService()
    service._wbi_keys = ("0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210")
    signed = service._sign({"avid": 1, "cid": 2})
    assert set(signed) == {"avid", "cid", "wts", "w_rid"}
    assert len(signed["w_rid"]) == 32
    assert len(MIXIN_KEY_ENC_TAB) == 64


def test_api_error_maps_to_user_facing_message(user_copy_checker):
    service = make_service({"web-interface/view": {"code": -404, "message": "啥都木有"}})
    with pytest.raises(AppError) as exc_info:
        service.probe("https://www.bilibili.com/video/BV1VVhk6pEiR")
    assert exc_info.value.code == "VIDEO_INFO_FAILED"
    user_copy_checker(exc_info.value.message)


# ---------------------------------------------------------------------------
# 接线检查（这类问题只有真实运行时才暴露：改了解析器但没接到处理器上）
# ---------------------------------------------------------------------------


def test_app_wires_api_backend_into_processor(settings):
    import dataclasses

    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.services.download_service import DownloadService

    api_app = create_app(dataclasses.replace(settings, platform_backend="api"))
    ytdlp_app = create_app(dataclasses.replace(settings, platform_backend="ytdlp"))

    assert isinstance(api_app.state.processor.platform_media_service, BilibiliApiService)
    assert isinstance(ytdlp_app.state.processor.platform_media_service, DownloadService)
    # 两个应用都要能正常启动
    for app in (api_app, ytdlp_app):
        app.state.processor.submit = lambda task_id: None
        with TestClient(app):
            pass
