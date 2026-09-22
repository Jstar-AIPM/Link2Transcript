from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_unknown_task_has_unified_error(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/tasks/07ba2677-e78f-4aa5-939f-aa41bb4cf11a")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "TASK_NOT_FOUND", "message": "未找到该任务"}
    }

