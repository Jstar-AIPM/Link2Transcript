from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_unknown_task_has_unified_error(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/tasks/07ba2677-e78f-4aa5-939f-aa41bb4cf11a")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "TASK_NOT_FOUND"
    assert body["error"]["message"] == "未找到该任务"
    # 阶段 6：错误体带追踪号，便于用户报错时提供编号（且与响应头一致）
    assert len(body["error"]["trace_id"]) == 8
    assert response.headers["X-Trace-Id"] == body["error"]["trace_id"]

