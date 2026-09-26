from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_missing_upload_uses_unified_validation_error(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/v1/tasks")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_backend_is_api_only_and_public_config_is_available(settings):
    """后端不再托管页面（界面是独立的前端服务），只提供 API 与公开配置。"""
    app = create_app(settings)
    with TestClient(app) as client:
        root = client.get("/")
        config = client.get("/api/v1/config")
    assert root.status_code == 404
    assert config.status_code == 200
    body = config.json()
    assert body["max_upload_mb"] == 1
    assert body["task_poll_interval_seconds"] == 1
    assert body["max_media_minutes"] == 180
