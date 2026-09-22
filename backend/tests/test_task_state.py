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


def test_incomplete_task_is_marked_failed_after_restart(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    service = TaskService(tasks_dir)
    record = make_task(service)
    service.transition(record.task_id, TaskStatus.VALIDATING)
    restarted_service = TaskService(tasks_dir)
    assert restarted_service.recover_incomplete() == 1
    restored = restarted_service.get(record.task_id)
    assert restored.status == TaskStatus.FAILED
    assert restored.error is not None
    assert restored.error.code == "TASK_INTERRUPTED"

