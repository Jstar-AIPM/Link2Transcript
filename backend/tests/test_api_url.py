from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.schemas.task import Platform, SourceType, TaskStatus
from backend.app.services.task_service import TaskService


BILIBILI_URL = "https://www.bilibili.com/video/BV1BqhB6nEdN"


@pytest.fixture
def client(settings):
    app = create_app(settings)
    submitted: list[str] = []
    app.state.processor.submit = submitted.append
    with TestClient(app) as test_client:
        test_client.app_ref = app
        test_client.submitted = submitted
        yield test_client


def test_valid_bilibili_url_creates_platform_task(client, settings):
    response = client.post("/api/v1/tasks/from-url", json={"url": BILIBILI_URL})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"

    assert client.submitted == [body["task_id"]]
    record = TaskService(settings.tasks_dir).get(body["task_id"])
    assert record.source_type == SourceType.PLATFORM_URL
    assert record.platform == Platform.BILIBILI
    assert record.status == TaskStatus.PENDING
    assert record.source_url == BILIBILI_URL
    assert record.resolved_url == BILIBILI_URL


def test_url_task_starts_with_placeholder_title_until_probe(client, settings):
    response = client.post("/api/v1/tasks/from-url", json={"url": BILIBILI_URL})
    task_id = response.json()["task_id"]
    status = client.get(f"/api/v1/tasks/{task_id}").json()
    assert status["source_type"] == "platform_url"
    assert status["platform"] == "bilibili"
    assert status["processing_method_label"] == "语音转写"
    assert status["artifacts"] == {"markdown": None, "txt": None}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.douyin.com/video/123456",
        "https://www.youtube.com/watch?v=abcdefg",
        "https://www.bilibili.com.evil.com/video/BV1BqhB6nEdN",
    ],
)
def test_non_bilibili_url_is_rejected_before_creating_task(
    client, settings, url, user_copy_checker
):
    response = client.post("/api/v1/tasks/from-url", json={"url": url})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED_PLATFORM"
    user_copy_checker(error["message"])
    assert list(settings.tasks_dir.glob("*.json")) == []


@pytest.mark.parametrize("url", ["", "   ", "not a url", "https://www.bilibili.com/"])
def test_invalid_url_is_rejected_before_creating_task(client, settings, url, user_copy_checker):
    response = client.post("/api/v1/tasks/from-url", json={"url": url})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_SOURCE_URL"
    user_copy_checker(error["message"])
    assert list(settings.tasks_dir.glob("*.json")) == []


def test_missing_url_field_uses_unified_validation_error(client):
    response = client.post("/api/v1/tasks/from-url", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_short_link_is_resolved_before_task_creation(client, settings):
    client.app_ref.state.platform_service._short_link_resolver = lambda url: BILIBILI_URL
    response = client.post("/api/v1/tasks/from-url", json={"url": "https://b23.tv/abcdefg"})
    assert response.status_code == 202
    record = TaskService(settings.tasks_dir).get(response.json()["task_id"])
    assert record.source_url == "https://b23.tv/abcdefg"
    assert record.resolved_url == BILIBILI_URL


def test_pasted_share_text_is_accepted_and_url_extracted(client, settings):
    """用户只做“粘贴”动作：输入框里可能是标题 + 链接 + 口令的一整段文字。"""
    text = (
        "3.30 复制打开抖音，看看【朴素之道的作品】如何练好字，学好英语口语 "
        f"{BILIBILI_URL}/ X@M.Wz :7pm kpD:/ 03/17"
    )
    response = client.post("/api/v1/tasks/from-url", json={"url": text})
    assert response.status_code == 202
    record = TaskService(settings.tasks_dir).get(response.json()["task_id"])
    # 存下来的是提取出来的链接，而不是整段文案
    assert record.resolved_url == f"{BILIBILI_URL}/"
    assert record.source_url == f"{BILIBILI_URL}/"
