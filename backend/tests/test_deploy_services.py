"""阶段 6 上线适配的单元测试：存储抽象 / 备份恢复 / 保留期清理 / 追踪号。

全部离线可跑：TOS 之外的逻辑用 `LocalStorage`（备份到本地目录）来验证，
TOS 客户端本身不做真实网络调用（其参数已按官方文档固定，真机验证放在部署阶段）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import AppError
from backend.app.main import create_app
from backend.app.schemas.task import MediaType, TaskStatus
from backend.app.services.backup_service import BackupService
from backend.app.services.cleanup_service import CleanupService
from backend.app.services.storage_service import (
    DisabledStorage,
    LocalStorage,
    build_storage,
)
from backend.app.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 存储抽象
# ---------------------------------------------------------------------------


def test_local_storage_roundtrip(tmp_path: Path):
    storage = LocalStorage(tmp_path / "bucket")
    source = tmp_path / "a.txt"
    source.write_text("内容", encoding="utf-8")

    storage.put_file("outputs/t1/a.txt", source)
    assert storage.list_keys("outputs/") == ["outputs/t1/a.txt"]

    target = tmp_path / "restored.txt"
    assert storage.get_file("outputs/t1/a.txt", target) is True
    assert target.read_text(encoding="utf-8") == "内容"

    assert storage.get_file("outputs/missing.txt", tmp_path / "x.txt") is False
    storage.delete("outputs/t1/a.txt")
    assert storage.list_keys("") == []


def test_local_storage_rejects_path_escape(tmp_path: Path):
    storage = LocalStorage(tmp_path / "bucket")
    with pytest.raises(AppError):
        storage.put_file("../escape.txt", tmp_path / "a.txt")


def test_disabled_storage_is_safe_noop(tmp_path: Path):
    storage = DisabledStorage()
    assert storage.enabled is False
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    storage.put_file("k", source)
    assert storage.list_keys("") == []
    assert storage.get_file("k", tmp_path / "out.txt") is False


def test_build_storage_falls_back_to_disabled(settings, tmp_path: Path):
    import dataclasses

    assert isinstance(build_storage(settings), DisabledStorage)

    local = dataclasses.replace(settings, storage_provider="local", storage_local_dir=tmp_path / "b")
    assert isinstance(build_storage(local), LocalStorage)

    # TOS 参数不完整时退化为 disabled，而不是带着空密钥去连
    incomplete = dataclasses.replace(settings, storage_provider="tos", tos_bucket="")
    assert isinstance(build_storage(incomplete), DisabledStorage)


# ---------------------------------------------------------------------------
# 备份与恢复
# ---------------------------------------------------------------------------


def make_business_data(
    data_dir: Path, task_id: str = "t1", *, with_task_record: bool = True
) -> None:
    """造一个任务的各类文件。``with_task_record=False`` 用于已经存在真实任务记录的场景。"""
    (data_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (data_dir / "outputs" / task_id).mkdir(parents=True, exist_ok=True)
    (data_dir / "downloads" / task_id).mkdir(parents=True, exist_ok=True)
    (data_dir / "audio" / task_id).mkdir(parents=True, exist_ok=True)
    if with_task_record:
        (data_dir / "tasks" / f"{task_id}.json").write_text('{"task_id": "t1"}', encoding="utf-8")
    (data_dir / "outputs" / task_id / "segments.jsonl").write_text("{}", encoding="utf-8")
    (data_dir / "outputs" / task_id / "transcript.md").write_text("# 标题", encoding="utf-8")
    (data_dir / "downloads" / task_id / "audio.m4a").write_bytes(b"big")
    (data_dir / "audio" / task_id / "audio.wav").write_bytes(b"big")


def test_backup_only_business_data_and_is_incremental(tmp_path: Path):
    data_dir = tmp_path / "data"
    make_business_data(data_dir)
    storage = LocalStorage(tmp_path / "bucket")
    service = BackupService(data_dir=data_dir, storage=storage)

    first = service.backup_all()
    assert first.uploaded == 3  # tasks/*.json + outputs 下两个文件
    keys = storage.list_keys("")
    assert "tasks/t1.json" in keys
    assert "outputs/t1/transcript.md" in keys
    # 中间音频不备份（可重新下载/提取）
    assert not any(key.startswith(("downloads/", "audio/")) for key in keys)

    second = service.backup_all()
    assert second.uploaded == 0 and second.skipped == 3  # 未变更不再上传

    (data_dir / "outputs" / "t1" / "transcript.md").write_text("# 改过了", encoding="utf-8")
    third = service.backup_all()
    assert third.uploaded == 1


def test_restore_only_fills_missing_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    make_business_data(data_dir)
    storage = LocalStorage(tmp_path / "bucket")
    BackupService(data_dir=data_dir, storage=storage).backup_all()

    # 模拟实例被回收：/tmp 数据全没了
    import shutil

    shutil.rmtree(data_dir)
    data_dir.mkdir()

    restored = BackupService(data_dir=data_dir, storage=storage).restore_missing()
    assert restored == 3
    assert (data_dir / "tasks" / "t1.json").is_file()
    assert (data_dir / "outputs" / "t1" / "segments.jsonl").is_file()


def test_backup_task_is_scoped_to_one_task(tmp_path: Path):
    data_dir = tmp_path / "data"
    make_business_data(data_dir, "t1")
    make_business_data(data_dir, "t2")
    storage = LocalStorage(tmp_path / "bucket")
    service = BackupService(data_dir=data_dir, storage=storage)

    result = service.backup_task("t1")
    assert result.uploaded == 3
    keys = storage.list_keys("")
    assert all("t2" not in key for key in keys)


def test_backup_failure_does_not_raise(tmp_path: Path):
    class BrokenStorage:
        enabled = True

        def put_file(self, key, path):
            raise OSError("disk full")

        def get_file(self, key, path):
            return False

        def list_keys(self, prefix=""):
            return []

        def delete(self, key):
            return None

    data_dir = tmp_path / "data"
    make_business_data(data_dir)
    result = BackupService(data_dir=data_dir, storage=BrokenStorage()).backup_all()
    assert result.failed == 3 and result.uploaded == 0


# ---------------------------------------------------------------------------
# 保留期清理
# ---------------------------------------------------------------------------


def make_task(service: TaskService, *, status: TaskStatus, updated_at: datetime) -> str:
    """造一个历史任务：状态与「最后更新时间」直接写到磁盘。

    为什么直接改文件：`TaskService` 每次写入都会把 `updated_at` 刷成当前时间
    （这是它的正常职责），所以"把任务变旧"只能绕过它来构造。
    """
    task_id = service.new_task_id()
    service.create(
        task_id=task_id,
        original_filename="demo.mp3",
        stored_filename="source.mp3",
        media_type=MediaType.AUDIO,
        content_type="audio/mpeg",
        size_bytes=1,
    )
    task_dir = service.tasks_dir
    record = json.loads((task_dir / f"{task_id}.json").read_text(encoding="utf-8"))
    record["status"] = status.value
    record["progress_stage"] = status.value
    record["updated_at"] = updated_at.isoformat()
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return task_id


def test_cleanup_removes_expired_terminal_tasks_only(tmp_path: Path):
    data_dir = tmp_path / "data"
    service = TaskService(data_dir / "tasks")
    now = datetime.now().astimezone()

    expired_done = make_task(service, status=TaskStatus.SUCCEEDED, updated_at=now - timedelta(days=10))
    expired_running = make_task(service, status=TaskStatus.TRANSCRIBING, updated_at=now - timedelta(days=10))
    fresh_done = make_task(service, status=TaskStatus.SUCCEEDED, updated_at=now)
    for task_id in (expired_done, expired_running, fresh_done):
        # 任务记录已由 make_task 写好（含真实的 updated_at），这里只造产物与中间文件
        make_business_data(data_dir, task_id, with_task_record=False)

    storage = LocalStorage(tmp_path / "bucket")
    backup = BackupService(data_dir=data_dir, storage=storage)
    backup.backup_all()

    cleaner = CleanupService(
        data_dir=data_dir,
        task_service=service,
        storage=storage,
        retention_days=7,
        backup_service=backup,
    )
    cleaned = cleaner.cleanup_expired(now=now)

    assert cleaned == [expired_done]
    # 本地数据全清
    assert not (data_dir / "tasks" / f"{expired_done}.json").exists()
    assert not (data_dir / "outputs" / expired_done).exists()
    assert not (data_dir / "downloads" / expired_done).exists()
    # 未超期 / 非终态 / 远端未被误删
    assert (data_dir / "tasks" / f"{fresh_done}.json").exists()
    assert (data_dir / "tasks" / f"{expired_running}.json").exists()
    assert f"tasks/{expired_done}.json" not in storage.list_keys("")
    assert f"tasks/{fresh_done}.json" in storage.list_keys("")


def test_cleanup_disabled_when_retention_is_zero(tmp_path: Path):
    data_dir = tmp_path / "data"
    service = TaskService(data_dir / "tasks")
    now = datetime.now().astimezone()
    task_id = make_task(service, status=TaskStatus.SUCCEEDED, updated_at=now - timedelta(days=99))
    make_business_data(data_dir, task_id)

    cleaner = CleanupService(
        data_dir=data_dir,
        task_service=service,
        storage=DisabledStorage(),
        retention_days=0,
    )
    assert cleaner.cleanup_expired(now=now) == []
    assert (data_dir / "tasks" / f"{task_id}.json").exists()


# ---------------------------------------------------------------------------
# 追踪号
# ---------------------------------------------------------------------------


def test_trace_id_in_header_and_error_body(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        ok = client.get("/api/v1/config")
        missing = client.get("/api/v1/tasks/07ba2677-e78f-4aa5-939f-aa41bb4cf11a")

    trace_id = ok.headers["X-Trace-Id"]
    assert len(trace_id) == 8 and all(c in "0123456789abcdef" for c in trace_id)
    assert missing.headers["X-Trace-Id"] == missing.json()["error"]["trace_id"]


def test_startup_restores_backup_and_cleans_expired(tmp_path: Path):
    """端到端：实例被回收后重启，能从对象存储恢复数据。"""
    import dataclasses

    from backend.tests.conftest import settings as _  # noqa: F401  (保持 fixture 可导入)

    data_dir = tmp_path / "data"
    make_business_data(data_dir)
    tasks = TaskService(data_dir / "tasks")
    now = datetime.now().astimezone()
    old_id = make_task(tasks, status=TaskStatus.SUCCEEDED, updated_at=now - timedelta(days=30))
    make_business_data(data_dir, old_id, with_task_record=False)

    bucket = tmp_path / "bucket"
    seed = BackupService(data_dir=data_dir, storage=LocalStorage(bucket))
    seed.backup_all()

    import shutil

    shutil.rmtree(data_dir)
    data_dir.mkdir()

    from backend.app.core.config import Settings

    fresh = Settings(
        app_env="test",
        data_dir=data_dir,
        max_upload_mb=1,
        whisper_model="tiny",
        whisper_device="cpu",
        whisper_compute_type="int8",
        task_poll_interval_seconds=1,
        task_max_workers=1,
        max_media_minutes=180,
        max_download_mb=1,
        storage_provider="local",
        storage_local_dir=bucket,
        retention_days=7,
    )
    app = create_app(fresh)
    app.state.processor.submit = lambda task_id: None
    with TestClient(app):
        pass

    # 启动时已从对象存储恢复；且 30 天前的任务被清理
    assert (data_dir / "tasks" / "t1.json").is_file() or (data_dir / "tasks" / f"{old_id}.json").exists()
    assert not (data_dir / "tasks" / f"{old_id}.json").exists()


def test_task_record_mirror_is_throttled_but_always_on_state_change(tmp_path: Path):
    """任务记录写穿到对象存储：同状态内节流，状态变化立刻上传。"""
    from backend.app.services.storage_service import LocalStorage
    from backend.app.services.task_service import TaskService

    data_dir = tmp_path / "data"
    storage = LocalStorage(tmp_path / "bucket")
    service = TaskService(data_dir / "tasks", storage=storage, data_dir=data_dir)
    service.mirror_interval_seconds = 3600.0  # 放大间隔，验证"同状态只传一次"

    task_id = service.new_task_id()
    service.create(
        task_id=task_id,
        original_filename="a.mp3",
        stored_filename="source.mp3",
        media_type="audio",
        content_type="audio/mpeg",
        size_bytes=1,
    )
    assert f"tasks/{task_id}.json" in storage.list_keys("")

    # 同状态内的多次进度写回不应反复上传（这里用文件内容变化来验证是否被覆盖）
    storage.put_file(f"tasks/{task_id}.json", data_dir / "tasks" / f"{task_id}.json")
    before = (storage.root / f"tasks/{task_id}.json").read_text(encoding="utf-8")
    service.set_progress(task_id, segment_count=5, transcribed_seconds=10.0)
    after = (storage.root / f"tasks/{task_id}.json").read_text(encoding="utf-8")
    assert before == after, "同状态内的进度写回被节流，不应上传"

    # 状态变化 → 立刻上传
    service.transition(task_id, TaskStatus.VALIDATING)
    updated = (storage.root / f"tasks/{task_id}.json").read_text(encoding="utf-8")
    assert '"status": "validating"' in updated
