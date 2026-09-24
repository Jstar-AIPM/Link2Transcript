"""产物保留期与清理（阶段 6 上线适配）。

线上必须清理，否则 `/tmp` 与对象存储会无限增长（阶段 1 起就登记了这条待办）。

清理规则：

1. 只清理**终态**任务（`succeeded` / `failed` / `cancelled`）—— 非终态跳过；
2. 按任务记录里的 `updated_at` 判断是否超过 `RETENTION_DAYS`；
3. 一个任务的四类数据一起清：`downloads/{id}`、`audio/{id}`、`outputs/{id}`、`tasks/{id}.json`；
4. 同时删除对象存储上的同名键（否则远端会一直留着旧产物）；
5. `RETENTION_DAYS=0` 表示不清理（本地开发默认），线上建议 7。
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.schemas.task import TaskStatus
from backend.app.services.backup_service import BACKUP_DIRS, BackupService
from backend.app.services.storage_service import StorageBackend
from backend.app.services.task_service import TaskService


logger = logging.getLogger(__name__)

#: 与任务相关的本地目录（tasks 记录单独处理）
TASK_DIRS = ("downloads", "audio", "outputs")
TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class CleanupService:
    def __init__(
        self,
        *,
        data_dir: Path,
        task_service: TaskService,
        storage: StorageBackend,
        retention_days: int,
        backup_service: BackupService | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.task_service = task_service
        self.storage = storage
        self.retention_days = retention_days
        self.backup_service = backup_service

    def cleanup_expired(self, *, now: datetime | None = None) -> list[str]:
        """删除超期任务的本地与远端数据，返回被清理的任务 id。"""
        if self.retention_days <= 0:
            return []
        reference = now or datetime.now().astimezone()
        deadline = reference - timedelta(days=self.retention_days)
        cleaned: list[str] = []

        for path in sorted(self.data_dir.joinpath("tasks").glob("*.json")):
            task_id = path.stem
            try:
                record = self.task_service.get(task_id)
            except Exception:  # noqa: BLE001 - 记录损坏时跳过，交给人工处理
                logger.warning("cleanup_skip_unreadable task_id=%s", task_id)
                continue
            if record.status not in TERMINAL:
                continue
            if record.updated_at > deadline:
                continue
            self._remove_task(task_id)
            cleaned.append(task_id)

        if cleaned:
            logger.info("cleanup_done removed=%s retention_days=%s", len(cleaned), self.retention_days)
        return cleaned

    def _remove_task(self, task_id: str) -> None:
        for directory in TASK_DIRS:
            shutil.rmtree(self.data_dir / directory / task_id, ignore_errors=True)
        (self.data_dir / "tasks" / f"{task_id}.json").unlink(missing_ok=True)

        if self.storage.enabled:
            for key in self._remote_keys(task_id):
                try:
                    self.storage.delete(key)
                except Exception:  # noqa: BLE001 - 远端删除失败不应中断清理
                    logger.warning("cleanup_remote_delete_failed key=%s", key, exc_info=True)
                if self.backup_service is not None:
                    self.backup_service.forget(key)

    def _remote_keys(self, task_id: str) -> list[str]:
        keys = [f"tasks/{task_id}.json"]
        try:
            keys.extend(self.storage.list_keys(prefix=f"outputs/{task_id}/"))
        except Exception:  # noqa: BLE001
            logger.warning("cleanup_remote_list_failed task_id=%s", task_id, exc_info=True)
        return keys
