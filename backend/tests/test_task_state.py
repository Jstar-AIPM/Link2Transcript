from pathlib import Path

import pytest

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType, TaskStatus
from backend.app.services.task_service import TaskService


def make_task(service: TaskService):
    task_id = service.new_task_id()
    return service.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=12,
    )


def test_audio_state_transitions_and_restart_read(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    service = TaskService(tasks_dir)
    record = make_task(service)
    for status in (
        TaskStatus.VALIDATING,
        TaskStatus.TRANSCRIBING,
        TaskStatus.EXPORTING,
        TaskStatus.SUCCEEDED,
    ):
        record = service.transition(record.task_id, status)
    restarted_service = TaskService(tasks_dir)
    restored = restarted_service.get(record.task_id)
    assert restored.status == TaskStatus.SUCCEEDED


def test_invalid_transition_is_rejected(tmp_path: Path):
    service = TaskService(tmp_path / "tasks")
    record = make_task(service)
    with pytest.raises(AppError) as exc_info:
        service.transition(record.task_id, TaskStatus.SUCCEEDED)
    assert exc_info.value.code == "INVALID_TASK_TRANSITION"


def test_invalid_transition_message_is_user_readable(tmp_path: Path):
    """状态名是内部术语，不能出现在用户可见文案里。"""
    import re

    service = TaskService(tmp_path / "tasks")
    record = make_task(service)
    with pytest.raises(AppError) as exc_info:
        service.transition(record.task_id, TaskStatus.SUCCEEDED)
    message = exc_info.value.message
    assert re.search(r"[\u4e00-\u9fff]", message)
    assert not re.search(r"[A-Za-z]", message), message


def test_requeue_for_resume_keeps_segments_and_progress(tmp_path: Path):
    """回到 pending 只重置状态，不动已落盘的片段与进度（阶段 3B）。"""
    service = TaskService(tmp_path / "tasks")
    record = make_task(service)
    service.transition(record.task_id, TaskStatus.VALIDATING)
    service.transition(record.task_id, TaskStatus.TRANSCRIBING)
    service.set_progress(record.task_id, segment_count=12, transcribed_seconds=34.5)

    requeued = service.requeue_for_resume(record.task_id)
    assert requeued.status == TaskStatus.PENDING
    assert requeued.segment_count == 12
    assert requeued.transcribed_seconds == 34.5
    assert requeued.stage_message is None


def test_incomplete_task_is_marked_failed_after_restart(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    service = TaskService(tasks_dir)
    record = make_task(service)
    service.transition(record.task_id, TaskStatus.VALIDATING)
    restarted_service = TaskService(tasks_dir)
    # 默认不做续跑（没传 is_resumable）：行为与阶段 1/2 一致
    assert restarted_service.recover_incomplete() == (1, [])
    restored = restarted_service.get(record.task_id)
    assert restored.status == TaskStatus.FAILED
    assert restored.error is not None
    assert restored.error.code == "TASK_INTERRUPTED"


def test_resumable_task_keeps_state_instead_of_failing(tmp_path: Path):
    """有已落盘片段的任务：重启后保持原状态，交给调用方重新入队续跑（阶段 3B）。"""
    tasks_dir = tmp_path / "tasks"
    service = TaskService(tasks_dir)
    resumable = make_task(service)
    service.transition(resumable.task_id, TaskStatus.VALIDATING)
    service.transition(resumable.task_id, TaskStatus.TRANSCRIBING)
    orphan = make_task(service)
    service.transition(orphan.task_id, TaskStatus.VALIDATING)

    restarted_service = TaskService(tasks_dir)
    interrupted, records = restarted_service.recover_incomplete(
        is_resumable=lambda record: record.task_id == resumable.task_id
    )

    assert interrupted == 1
    assert [record.task_id for record in records] == [resumable.task_id]
    kept = restarted_service.get(resumable.task_id)
    assert kept.status == TaskStatus.TRANSCRIBING
    assert kept.error is None
    assert restarted_service.get(orphan.task_id).status == TaskStatus.FAILED


def test_resumed_count_increments(tmp_path: Path):
    service = TaskService(tmp_path / "tasks")
    record = make_task(service)
    assert record.resumed_count == 0
    assert service.increment_resumed_count(record.task_id).resumed_count == 1
    assert service.increment_resumed_count(record.task_id).resumed_count == 2

