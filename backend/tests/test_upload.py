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
