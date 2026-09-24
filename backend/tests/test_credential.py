"""B 站登录态自检（阶段 6）测试。

字幕路径比语音转写快两个数量级，但依赖登录态；Cookie 失效时按设计会静默降级。
这个自检的价值就是把"静默降级"变成"看得见的告警 + 配置接口里的状态"。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.credential_service import BilibiliCredentialService


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_not_configured_when_no_cookie():
    service = BilibiliCredentialService("")
    assert service.status == "not_configured"
    assert service.verify() == "not_configured"


def test_valid_login(monkeypatch):
    service = BilibiliCredentialService("SESSDATA=abc")
    monkeypatch.setattr(
        "backend.app.services.credential_service.requests.get",
        lambda *a, **k: FakeResponse({"data": {"isLogin": True}}),
    )
    assert service.verify() == "valid"
    assert service.status == "valid"


def test_invalid_login_is_reported(monkeypatch, caplog):
    service = BilibiliCredentialService("SESSDATA=expired")
    monkeypatch.setattr(
        "backend.app.services.credential_service.requests.get",
        lambda *a, **k: FakeResponse({"data": {"isLogin": False}}),
    )
    with caplog.at_level("WARNING"):
        assert service.verify() == "invalid"
    assert any("bilibili_login_invalid" in record.message for record in caplog.records)


def test_network_failure_is_unknown_not_invalid(monkeypatch):
    """网络抖动不能被当成"凭据失效"，否则会误导用户去换 Cookie。"""

    def boom(*args, **kwargs):
        raise OSError("network down")

    service = BilibiliCredentialService("SESSDATA=abc")
    monkeypatch.setattr("backend.app.services.credential_service.requests.get", boom)
    assert service.verify() == "unknown"


def test_config_exposes_login_status(settings, monkeypatch):
    import dataclasses

    monkeypatch.setattr(
        "backend.app.services.credential_service.requests.get",
        lambda *a, **k: FakeResponse({"data": {"isLogin": False}}),
    )
    app = create_app(dataclasses.replace(settings, bilibili_cookie="SESSDATA=expired"))
    with TestClient(app) as client:
        # 启动时后台自检可能还没跑完，因此只断言字段存在且取值合法
        status_value = client.get("/api/v1/config").json()["bilibili_login"]
    assert status_value in {"unknown", "invalid", "valid", "not_configured"}
