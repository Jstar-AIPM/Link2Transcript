from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_supported_upload_creates_persistent_task(settings):
    app = create_app(settings)
    submitted: list[str] = []
    app.state.processor.submit = submitted.append
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tasks",
            files={"file": ("voice.mp3", b"fake-media", "audio/mpeg")},
        )
        body = response.json()
        status_response = client.get(f"/api/v1/tasks/{body['task_id']}")
    assert response.status_code == 202
    assert body["status"] == "pending"
    assert submitted == [body["task_id"]]
    assert (settings.tasks_dir / f"{body['task_id']}.json").is_file()
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["elapsed_seconds"] >= 0
    assert status["created_at"]


def test_unsupported_upload_is_rejected_without_task(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tasks",
            files={"file": ("notes.pdf", b"not-media", "application/pdf")},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert list(settings.tasks_dir.glob("*.json")) == []


def test_oversized_upload_is_rejected_without_task(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tasks",
            files={"file": ("large.wav", b"x" * (1024 * 1024 + 1), "audio/wav")},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert list(settings.tasks_dir.glob("*.json")) == []


# ---------------------------------------------------------------------------
# 线上关闭本地上传（veFaaS 请求体上限 16 MiB，线上 V1 只支持链接）
# ---------------------------------------------------------------------------


def test_local_upload_can_be_disabled_for_online(settings, user_copy_checker):
    import dataclasses

    disabled = dataclasses.replace(settings, enable_local_upload=False)
    disabled.ensure_directories()
    app = create_app(disabled)
    app.state.processor.submit = lambda task_id: None

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tasks",
            files={"file": ("demo.mp3", b"data", "audio/mpeg")},
        )
        config = client.get("/api/v1/config").json()

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "LOCAL_UPLOAD_DISABLED"
    assert "B 站视频链接" in body["error"]["message"]
    user_copy_checker(body["error"]["message"])
    # 前端据此隐藏上传入口
    assert config["enable_local_upload"] is False
    # 且不应留下任何失败的任务记录
    assert list(disabled.tasks_dir.glob("*.json")) == []


def test_local_upload_enabled_exposes_flag(settings):
    settings.ensure_directories()
    app = create_app(settings)
    app.state.processor.submit = lambda task_id: None
    with TestClient(app) as client:
        assert client.get("/api/v1/config").json()["enable_local_upload"] is True
