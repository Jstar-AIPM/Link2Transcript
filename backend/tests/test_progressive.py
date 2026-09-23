"""阶段 3A：转写过程实时反馈（逐段落盘 + 进度模型 + 增量接口）。

这些测试锁住四件事：

1. 片段在转写**过程中**就已落盘（不是等任务结束才写）；
2. 任务记录里的进度（片段数、已转写时长、百分比、阶段文案）随转写推进；
3. 失败时保留已生成片段，且**不**生成成功产物；
4. 增量接口的语义（after / limit / partial / has_more）在字幕与转写两条链路上一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import AppError
from backend.app.core.messages import (
    LOADING_MODEL_MESSAGE,
    TRANSCRIBING_MESSAGE,
    transcribing_progress_message,
)
from backend.app.main import create_app
from backend.app.schemas.task import (
    MediaType,
    TaskArtifacts,
    TaskStatus,
    TranscriptSegment,
)
from backend.app.services.segment_store import SEGMENTS_FILE_NAME, SegmentStore
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import Transcription

from backend.tests.test_processor import (
    FakeMediaService,
    build_processor,
    create_local_task,
)


SEGMENTS = [
    (0.0, 2.0, "第一段"),
    (2.0, 5.0, "第二段"),
    (5.0, 9.0, "第三段"),
]


class ProgressiveTranscriptionService:
    """逐个片段产出，并在每次回调之后立刻检查磁盘上的真实状态。

    这正是阶段 3 的核心诉求：转写还没结束，用户就应该能看到内容与进度。
    """

    def __init__(
        self,
        *,
        tasks: TaskService,
        task_id: str,
        store: SegmentStore,
        fail_after: int | None = None,
    ) -> None:
        self.tasks = tasks
        self.task_id = task_id
        self.store = store
        self.fail_after = fail_after
        self.stage_messages: list[str] = []
        # 每次片段回调后：磁盘上的片段数、记录里的片段数、状态文案
        self.observed: list[tuple[int, int, str | None]] = []

    def transcribe(self, audio_path: Path, on_segment=None, on_stage=None, **kwargs) -> Transcription:
        assert audio_path.is_file()
        if on_stage is not None:
            on_stage("loading_model")
            self.stage_messages.append(self.tasks.get(self.task_id).stage_message)
            on_stage("transcribing")
            self.stage_messages.append(self.tasks.get(self.task_id).stage_message)

        stored = []
        for index, (start, end, text) in enumerate(SEGMENTS):
            if self.fail_after is not None and index == self.fail_after:
                raise AppError("TRANSCRIPTION_FAILED", "转写未完成，请重试")
            segment = TranscriptSegment(start=start, end=end, text=text)
            stored.append(segment)
            if on_segment is not None:
                on_segment(segment)
            record = self.tasks.get(self.task_id)
            self.observed.append(
                (
                    len(self.store.read_all(self.task_id)),
                    record.segment_count,
                    record.stage_message,
                )
            )

        return Transcription(
            text="\n".join(segment.text for segment in stored),
            segments=stored,
            language="zh",
            duration_seconds=9.0,
        )


class LongMediaService(FakeMediaService):
    def __init__(self, duration_seconds: float = 9.0) -> None:
        super().__init__(duration_seconds=duration_seconds)


def progressive_processor(settings, *, fail_after=None, interval=0.0):
    """构造一个带“真实磁盘校验”的处理器，并把 tasks/store 暴露给断言使用。"""
    tasks, task_id = create_local_task(settings, MediaType.AUDIO)
    store = SegmentStore(settings.outputs_dir)
    service = ProgressiveTranscriptionService(
        tasks=tasks, task_id=task_id, store=store, fail_after=fail_after
    )
    processor = build_processor(
        settings,
        media_service=LongMediaService(),
        transcription_service=service,
        segment_store=store,
        progress_persist_interval_seconds=interval,
    )
    processor.process(task_id)
    processor.shutdown()
    return tasks, task_id, store, service


# ---------------------------------------------------------------------------
# 逐段落盘 + 进度模型
# ---------------------------------------------------------------------------


def test_segments_are_persisted_while_transcribing(settings):
    tasks, task_id, store, service = progressive_processor(settings)

    # 每一步回调之后，磁盘上都已经有对应片段：不是等任务结束才写。
    assert [observed[0] for observed in service.observed] == [1, 2, 3]
    assert [observed[1] for observed in service.observed] == [1, 2, 3]
    assert service.observed[-1][2] == transcribing_progress_message(3)

    record = tasks.get(task_id)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.segment_count == 3
    assert record.transcribed_seconds == 9.0
    assert record.stage_message is None  # 成功后不再显示转写中的文案
    assert record.partial_result_available is False
    assert store.path_for(task_id).is_file()


def test_first_segment_makes_progress_visible_immediately(settings):
    """首个片段不等节流周期：否则短任务会全程看不到进度。"""
    _, _, _, service = progressive_processor(settings, interval=3600.0)
    assert service.observed[0][1] == 1


def test_progress_write_back_is_throttled(settings):
    """片段落盘是逐条的，但任务记录不逐条重写（避免 O(n²) 写放大）。"""
    tasks, task_id, store, service = progressive_processor(settings, interval=3600.0)

    # 转写过程中记录只被写回一次（首片段），而磁盘上已有 3 个片段
    assert [observed[1] for observed in service.observed] == [1, 1, 1]
    assert store.count(task_id) == 3

    # 结束后强制写回最终进度，断点不滞后
    record = tasks.get(task_id)
    assert record.segment_count == 3
    assert record.transcribed_seconds == 9.0


def test_stage_message_covers_first_token_latency(settings):
    """"正在加载语音识别模型" 必须真实出现在首字延迟期间。"""
    _, _, _, service = progressive_processor(settings)
    assert service.stage_messages == [LOADING_MODEL_MESSAGE, TRANSCRIBING_MESSAGE]


def test_persisted_segments_match_exported_artifact(settings):
    """正常路径质量不变：导出内容与阶段 2 完全一致（同一个片段列表）。"""
    tasks, task_id, store, _ = progressive_processor(settings)
    record = tasks.get(task_id)
    result = json.loads(Path(record.artifacts.result).read_text(encoding="utf-8"))
    assert [segment["text"] for segment in result["segments"]] == ["第一段", "第二段", "第三段"]
    assert [segment.text for segment in store.read_all(task_id)] == ["第一段", "第二段", "第三段"]


# ---------------------------------------------------------------------------
# 失败保留部分结果
# ---------------------------------------------------------------------------


def test_failure_keeps_partial_result_and_produces_no_artifact(settings, user_copy_checker):
    tasks, task_id, store, service = progressive_processor(settings, fail_after=2)

    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.error is not None
    assert record.error.code == "TRANSCRIPTION_FAILED"
    user_copy_checker(record.error.message)

    # 已生成的两个片段保留，进度不丢弃
    assert record.partial_result_available is True
    assert record.segment_count == 2
    assert record.transcribed_seconds == 5.0
    assert [segment.text for segment in store.read_all(task_id)] == ["第一段", "第二段"]

    # “成功产物才可下载”：失败时不得留下半成品文件
    task_dir = settings.outputs_dir / task_id
    assert not (task_dir / "transcript.md").exists()
    assert not (task_dir / "transcript.txt").exists()
    assert not (task_dir / "result.json").exists()
    assert record.artifacts == TaskArtifacts()


def test_failure_without_any_segment_is_not_a_partial_result(settings):
    tasks, task_id, store, _ = progressive_processor(settings, fail_after=0)
    record = tasks.get(task_id)
    assert record.status == TaskStatus.FAILED
    assert record.partial_result_available is False
    assert store.count(task_id) == 0


# ---------------------------------------------------------------------------
# 增量接口
# ---------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.processor.submit = lambda task_id: None
    with TestClient(app) as test_client:
        test_client.app_ref = app
        yield test_client


def make_task(settings, **fields):
    """在应用启动之后造任务，避免被启动恢复流程改写状态。"""
    service = TaskService(settings.tasks_dir)
    task_id = service.new_task_id()
    record = service.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=5,
    )
    if fields:
        service._update(task_id, **fields)
    return task_id


def test_segments_endpoint_returns_increments(client, settings):
    task_id = make_task(settings)
    store = client.app_ref.state.segment_store
    for start, end, text in SEGMENTS:
        store.append(task_id, TranscriptSegment(start=start, end=end, text=text))
    TaskService(settings.tasks_dir).set_progress(
        task_id, segment_count=3, transcribed_seconds=9.0
    )

    first = client.get(f"/api/v1/tasks/{task_id}/segments?after=0&limit=2").json()
    assert first["total"] == 3
    assert first["next_after"] == 2
    assert first["has_more"] is True
    assert first["partial"] is True  # 尚未成功 → 部分结果
    assert [s["text"] for s in first["segments"]] == ["第一段", "第二段"]

    second = client.get(f"/api/v1/tasks/{task_id}/segments?after=2").json()
    assert second["next_after"] == 3
    assert second["has_more"] is False
    assert [s["text"] for s in second["segments"]] == ["第三段"]
    assert [s["index"] for s in second["segments"]] == [2]

    # 进度字段随状态响应一起返回
    status = client.get(f"/api/v1/tasks/{task_id}").json()
    assert status["segment_count"] == 3
    assert status["transcribed_seconds"] == 9.0
    assert status["stage_message"] == "任务已创建"  # 未设置细粒度文案时回落


def test_segments_endpoint_hides_partial_flag_after_success(client, settings):
    task_id = make_task(settings)
    store = client.app_ref.state.segment_store
    store.append(task_id, TranscriptSegment(start=0, end=9, text="全片"))
    tasks = TaskService(settings.tasks_dir)
    tasks.transition(task_id, TaskStatus.VALIDATING)
    tasks.transition(task_id, TaskStatus.TRANSCRIBING)
    tasks.transition(task_id, TaskStatus.EXPORTING)
    tasks.transition(task_id, TaskStatus.SUCCEEDED)

    body = client.get(f"/api/v1/tasks/{task_id}/segments").json()
    assert body["partial"] is False
    assert body["total"] == 1
    assert client.get(f"/api/v1/tasks/{task_id}").json()["progress_percent"] == 100.0


def test_segments_endpoint_serves_subtitle_tasks_from_artifact(client, settings):
    """字幕路径不经过转写：成功后片段仍应可通过同一个接口获取。"""
    task_id = make_task(settings)
    tasks = TaskService(settings.tasks_dir)
    tasks.transition(task_id, TaskStatus.VALIDATING)
    tasks.transition(task_id, TaskStatus.TRANSCRIBING)
    tasks.transition(task_id, TaskStatus.EXPORTING)
    tasks.transition(task_id, TaskStatus.SUCCEEDED)

    result = {
        "schema_version": 3,
        "task_id": task_id,
        "source_type": "local_file",
        "platform": "local",
        "original_filename": "demo.mp3",
        "media_type": "audio",
        "processing_method": "speech_to_text",
        "language": "zh",
        "duration_seconds": 9.0,
        "text": "第一段\n第二段",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "第一段"},
            {"start": 2.0, "end": 9.0, "text": "第二段"},
        ],
        "generated_at": "2026-09-23T12:00:00+08:00",
    }
    result_path = settings.outputs_dir / task_id / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    tasks._update(
        task_id,
        artifacts=TaskArtifacts(
            markdown=None, txt=None, result=str(result_path)
        ),
    )

    body = client.get(f"/api/v1/tasks/{task_id}/segments?after=1").json()
    assert body["partial"] is False
    assert body["total"] == 2
    assert [s["text"] for s in body["segments"]] == ["第二段"]


def test_segments_endpoint_without_segments_returns_empty(client, settings):
    task_id = make_task(settings)
    body = client.get(f"/api/v1/tasks/{task_id}/segments").json()
    assert body == {
        "task_id": task_id,
        "status": "pending",
        "partial": True,
        "total": 0,
        "next_after": 0,
        "has_more": False,
        "segments": [],
    }


def test_segments_endpoint_rejects_invalid_parameters(client, settings):
    task_id = make_task(settings)
    assert client.get(f"/api/v1/tasks/{task_id}/segments?after=-1").status_code == 422
    assert client.get(f"/api/v1/tasks/{task_id}/segments?limit=0").status_code == 422
    assert client.get(f"/api/v1/tasks/{task_id}/segments?limit=99999").status_code == 422


def test_segments_endpoint_unknown_task_is_not_found(client):
    response = client.get("/api/v1/tasks/2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11/segments")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_truncated_segment_line_does_not_break_status_or_api(client, settings):
    """进程被强杀留下的半截 JSON：接口照常返回前面的片段，进度也不受影响。"""
    task_id = make_task(settings)
    store = client.app_ref.state.segment_store
    store.append(task_id, TranscriptSegment(start=0, end=4, text="完整"))
    with store.path_for(task_id).open("a", encoding="utf-8") as handle:
        handle.write('{"index": 1, "start": 4.0, "end": 8.0, "tex')

    TaskService(settings.tasks_dir).set_progress(
        task_id, segment_count=1, transcribed_seconds=4.0
    )
    body = client.get(f"/api/v1/tasks/{task_id}/segments").json()
    assert body["total"] == 1
    assert [s["text"] for s in body["segments"]] == ["完整"]
    status = client.get(f"/api/v1/tasks/{task_id}").json()
    assert status["segment_count"] == 1
    assert (settings.outputs_dir / task_id / SEGMENTS_FILE_NAME).is_file()


def test_progress_percent_is_capped_in_progress_and_rounds_on_success(settings):
    """进度百分比：未完成封顶 99，成功固定 100。"""
    from backend.app.api.tasks import _progress_percent
    from backend.app.schemas.task import TaskRecord

    base = {
        "task_id": "2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11",
        "original_filename": "demo.mp3",
        "stored_filename": "source.mp3",
        "media_type": "audio",
        "created_at": "2026-09-23T12:00:00+08:00",
        "updated_at": "2026-09-23T12:00:00+08:00",
        "progress_stage": "transcribing",
    }
    record = TaskRecord(status=TaskStatus.TRANSCRIBING, media_duration_seconds=100.0,
                        transcribed_seconds=250.0, **base)
    assert _progress_percent(record) == 99.0

    record = TaskRecord(status=TaskStatus.TRANSCRIBING, media_duration_seconds=100.0,
                        transcribed_seconds=42.0, **base)
    assert _progress_percent(record) == 42.0

    record = TaskRecord(status=TaskStatus.TRANSCRIBING, media_duration_seconds=None,
                        transcribed_seconds=None, **base)
    assert _progress_percent(record) == 0.0

    record = TaskRecord(status=TaskStatus.SUCCEEDED, media_duration_seconds=100.0,
                        transcribed_seconds=88.0, **base)
    assert _progress_percent(record) == 100.0
