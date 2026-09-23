"""阶段 3D：取消任务。

取消的语义（与设计文档 D5 一致）：

- 用户点取消，任务**立即**进入终态 ``cancelled``（即使工作线程正在下载或解码）；
- 工作线程在下一个检查点（每个片段、每个阶段切换）发现已是终态，立刻停止；
- 已落盘的部分结果保留（``partial_result_available=true``），可继续通过片段接口读取；
- 但**不生成** ``transcript.md`` / ``txt`` / ``result.json``：半成品不应被当成完整逐字稿下载。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import AppError
from backend.app.main import create_app
from backend.app.schemas.task import MediaType, TaskArtifacts, TaskStatus, TranscriptSegment
from backend.app.services.segment_store import SegmentStore
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import Transcription

from backend.tests.test_processor import FakeMediaService, build_processor, create_local_task


ALL_SEGMENTS = [
    TranscriptSegment(start=0, end=2, text="第一段"),
    TranscriptSegment(start=2, end=4, text="第二段"),
    TranscriptSegment(start=4, end=6, text="第三段"),
    TranscriptSegment(start=6, end=8, text="第四段"),
]


class CancellingTranscriptionService:
    """在第 3 个片段产出后模拟“用户点了取消”，然后继续产出后续片段。"""

    def __init__(self, *, tasks: TaskService, task_id: str) -> None:
        self.tasks = tasks
        self.task_id = task_id
        #: 已经交给回调的片段（不论回调是否因取消而中断）
        self.attempted: list[str] = []

    def transcribe(self, audio_path: Path, on_segment=None, **kwargs) -> Transcription:
        for index, segment in enumerate(ALL_SEGMENTS):
            if index == 3:
                self.tasks.request_cancel(self.task_id)
            self.attempted.append(segment.text)
            if on_segment is not None:
                on_segment(segment)
        return Transcription(
            text="\n".join(s.text for s in ALL_SEGMENTS),
            segments=list(ALL_SEGMENTS),
            language="zh",
            duration_seconds=8.0,
        )


def test_cancel_keeps_partial_result_without_artifacts(settings, user_copy_checker):
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    store = SegmentStore(settings.outputs_dir)
    service = CancellingTranscriptionService(tasks=tasks, task_id=task_id)
    processor = build_processor(
        settings,
        media_service=FakeMediaService(duration_seconds=8.0),
        transcription_service=service,
        segment_store=store,
        progress_persist_interval_seconds=0.0,
    )
    processor.process(task_id)
    processor.shutdown()

    record = tasks.get(task_id)
    assert record.status == TaskStatus.CANCELLED
    assert record.error is None  # 取消不是失败
    assert record.partial_result_available is True
    # 第 4 个片段是在取消之后才产出的，不能落盘
    assert [segment.text for segment in store.read_all(task_id)] == ["第一段", "第二段", "第三段"]
    assert record.segment_count == 3
    assert record.transcribed_seconds == 6.0
    assert service.attempted == ["第一段", "第二段", "第三段", "第四段"]  # 解码在下一个检查点才停

    task_dir = settings.outputs_dir / task_id
    assert not (task_dir / "transcript.md").exists()
    assert not (task_dir / "transcript.txt").exists()
    assert not (task_dir / "result.json").exists()
    assert record.artifacts == TaskArtifacts()


def test_transition_raises_once_cancelled(tmp_path: Path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.new_task_id()
    service.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=1,
    )
    service.transition(task_id, TaskStatus.VALIDATING)
    service.transition(task_id, TaskStatus.TRANSCRIBING)
    service.request_cancel(task_id)

    # 工作线程在检查点用这个异常停下来
    with pytest.raises(AppError) as exc_info:
        service.transition(task_id, TaskStatus.EXPORTING)
    assert exc_info.value.code == "TASK_CANCELLED"

    # 取消后 fail() 不得改写终态
    record = service.fail(task_id, code="INTERNAL_PROCESSING_ERROR", message="处理失败，请重试")
    assert record.status == TaskStatus.CANCELLED
    assert record.error is None


def test_cancel_rejects_finished_tasks(tmp_path: Path):
    service = TaskService(tmp_path / "tasks")
    task_id = service.new_task_id()
    service.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=1,
    )
    for status in (TaskStatus.VALIDATING, TaskStatus.TRANSCRIBING, TaskStatus.EXPORTING,
                   TaskStatus.SUCCEEDED):
        service.transition(task_id, status)

    with pytest.raises(AppError) as exc_info:
        service.request_cancel(task_id)
    assert exc_info.value.code == "TASK_ALREADY_FINISHED"
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.processor.submit = lambda task_id: None
    with TestClient(app) as test_client:
        test_client.app_ref = app
        yield test_client


def make_running_task(settings):
    tasks = TaskService(settings.tasks_dir)
    task_id = tasks.new_task_id()
    tasks.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=1,
    )
    tasks.transition(task_id, TaskStatus.VALIDATING)
    tasks.transition(task_id, TaskStatus.TRANSCRIBING)
    return tasks, task_id


def test_cancel_endpoint_returns_cancelled_status(client, settings, user_copy_checker):
    tasks, task_id = make_running_task(settings)
    store = client.app_ref.state.segment_store
    store.append(task_id, ALL_SEGMENTS[0])
    tasks.set_media_duration(task_id, 100.0)
    tasks.set_progress(task_id, segment_count=1, transcribed_seconds=2.0)

    response = client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancellable"] is False
    assert body["stage_message"] == "任务已取消"
    # 进度与磁盘对齐（进度写回是节流的，工作线程要到下一个检查点才停）
    assert body["progress_percent"] == 2.0
    assert body["segment_count"] == 1
    assert body["partial_result_available"] is True
    user_copy_checker(body["stage_message"])

    # 部分结果仍然可读，且不会被当成完整逐字稿
    segments = client.get(f"/api/v1/tasks/{task_id}/segments").json()
    assert segments["partial"] is True
    assert [s["text"] for s in segments["segments"]] == ["第一段"]
    assert client.get(f"/api/v1/tasks/{task_id}/download/markdown").status_code == 409
    assert client.get(f"/api/v1/tasks/{task_id}/result").status_code == 409


def test_cancel_endpoint_is_idempotent_conflict_when_finished(client, settings):
    tasks, task_id = make_running_task(settings)
    assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 200
    second = client.post(f"/api/v1/tasks/{task_id}/cancel")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TASK_ALREADY_FINISHED"


def test_cancel_endpoint_unknown_task(client):
    response = client.post("/api/v1/tasks/2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11/cancel")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_status_exposes_cancellable_flag(client, settings):
    tasks, task_id = make_running_task(settings)
    assert client.get(f"/api/v1/tasks/{task_id}").json()["cancellable"] is True
    tasks.transition(task_id, TaskStatus.EXPORTING)
    tasks.transition(task_id, TaskStatus.SUCCEEDED)
    body = client.get(f"/api/v1/tasks/{task_id}").json()
    assert body["cancellable"] is False
    assert body["status"] == "succeeded"


def test_cancelled_task_is_not_resumed_on_restart(client, settings):
    """取消是终态：重启后不会又被自动续跑。"""
    tasks, task_id = make_running_task(settings)
    tasks.request_cancel(task_id)
    assert SegmentStore(settings.outputs_dir)  # 目录存在即可

    app = create_app(settings)
    submitted: list[str] = []
    app.state.processor.submit = submitted.append
    with TestClient(app):
        pass

    assert submitted == []
    assert TaskService(settings.tasks_dir).get(task_id).status == TaskStatus.CANCELLED


def test_cancelled_task_is_reported_as_terminal_in_status_response(client, settings):
    """终态判定要包含 cancelled：否则页面会一直显示“任务仍在运行”。"""
    import json as _json

    tasks, task_id = make_running_task(settings)
    tasks.request_cancel(task_id)
    body = client.get(f"/api/v1/tasks/{task_id}").json()
    assert body["status"] == "cancelled"
    assert body["estimated_remaining_seconds"] is None
    # 终态任务的耗时应该是冻结的
    detail = json.loads(_json.dumps(body))
    assert detail["elapsed_seconds"] >= 0
