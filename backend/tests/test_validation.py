from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_missing_upload_uses_unified_validation_error(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/v1/tasks")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_acceptance_page_and_public_config_are_available(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/")
        config = client.get("/api/v1/config")
    assert page.status_code == 200
    assert "逐字稿提取器" in page.text
    body = config.json()
    assert body["max_upload_mb"] == 1
    assert body["task_poll_interval_seconds"] == 1
    assert body["max_media_minutes"] == 180
