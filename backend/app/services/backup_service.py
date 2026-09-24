"""备份与恢复（阶段 6 上线适配）。

线上（veFaaS）函数实例除 `/tmp` 外只读，而 `/tmp` 在实例回收后即丢。
本模块负责把**业务数据**（任务记录 + 逐字稿产物）定期备份到对象存储，
并在服务启动时把云端已有的数据恢复回本地。

三条设计规则：

1. **只备份业务数据**：`tasks/`（任务记录 JSON）与 `outputs/`（片段 JSONL、产物）。
   中间音频（`downloads/`、`audio/`）不备份 —— 它们可以重新下载或重新提取，体积却很大；
2. **增量**：按「文件大小 + 修改时间」判断是否需要重新上传，避免每次全量；
3. **恢复只补缺失**：启动时只下载本地不存在的键，绝不用云端覆盖本地较新的文件。

未配置对象存储时（`STORAGE_PROVIDER=disabled`）全部是空操作，本地开发行为不变。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.app.services.storage_service import StorageBackend


logger = logging.getLogger(__name__)

#: 备份范围（键前缀与本地子目录一一对应）
BACKUP_DIRS = ("tasks", "outputs")
#: 除目录外还要备份的顶层文件（邀请码与会话记录同样属于业务数据）
BACKUP_FILES = ("invite-codes.json",)
#: 单个文件超过这个大小就不备份（逐字稿产物是文本，正常远小于它）
MAX_BACKUP_FILE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class BackupResult:
    uploaded: int
    skipped: int
    failed: int


class BackupService:
    def __init__(self, *, data_dir: Path, storage: StorageBackend) -> None:
        self.data_dir = data_dir
        self.storage = storage
        # key -> (size, mtime) 上次成功上传时的快照，用于增量判断
        self._synced: dict[str, tuple[int, float]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 备份

    def backup_all(self) -> BackupResult:
        """把所有变更过的业务文件上传到对象存储。"""
        if not self.storage.enabled:
            return BackupResult(uploaded=0, skipped=0, failed=0)
        uploaded = skipped = failed = 0
        for relative, path in self._iter_business_files():
            if self._is_synced(relative, path):
                skipped += 1
                continue
            try:
                self.storage.put_file(relative, path)
            except Exception:  # noqa: BLE001 - 备份失败不能影响主流程
                failed += 1
                logger.warning("backup_upload_failed key=%s", relative, exc_info=True)
                continue
            self._mark_synced(relative, path)
            uploaded += 1
        if uploaded or failed:
            logger.info("backup_done uploaded=%s skipped=%s failed=%s", uploaded, skipped, failed)
        return BackupResult(uploaded=uploaded, skipped=skipped, failed=failed)

    def backup_task(self, task_id: str) -> BackupResult:
        """任务进入终态时立即备份该任务（保证结果不丢，不等下一次周期）。"""
        if not self.storage.enabled:
            return BackupResult(uploaded=0, skipped=0, failed=0)
        uploaded = skipped = failed = 0
        for relative, path in self._iter_business_files(task_id=task_id):
            if self._is_synced(relative, path):
                skipped += 1
                continue
            try:
                self.storage.put_file(relative, path)
            except Exception:  # noqa: BLE001
                failed += 1
                logger.warning("backup_task_upload_failed key=%s", relative, exc_info=True)
                continue
            self._mark_synced(relative, path)
            uploaded += 1
        return BackupResult(uploaded=uploaded, skipped=skipped, failed=failed)

    # ------------------------------------------------------------------ 恢复

    def restore_missing(self) -> int:
        """启动时把云端已有、本地缺失的业务文件下载回来。返回恢复的文件数。"""
        if not self.storage.enabled:
            return 0
        restored = 0
        for name in BACKUP_FILES:
            target = self.data_dir / name
            if target.exists():
                continue
            try:
                if self.storage.get_file(name, target):
                    restored += 1
                    self._mark_synced(name, target)
            except Exception:  # noqa: BLE001
                logger.warning("backup_restore_failed key=%s", name, exc_info=True)

        for directory in BACKUP_DIRS:
            try:
                keys = self.storage.list_keys(prefix=f"{directory}/")
            except Exception:  # noqa: BLE001 - 读不到远端不应阻止服务启动
                logger.warning("backup_list_failed prefix=%s", directory, exc_info=True)
                return restored
            for key in keys:
                target = self.data_dir / key
                if target.exists():
                    continue
                try:
                    if self.storage.get_file(key, target):
                        restored += 1
                        self._mark_synced(key, target)
                except Exception:  # noqa: BLE001
                    logger.warning("backup_restore_failed key=%s", key, exc_info=True)
        if restored:
            logger.info("backup_restored files=%s", restored)
        return restored

    # ------------------------------------------------------------------ 内部

    def _iter_business_files(self, task_id: str | None = None):
        # 邀请码/会话文件与任务无关，只在全量备份时处理
        if task_id is None:
            for name in BACKUP_FILES:
                path = self.data_dir / name
                if path.is_file():
                    yield name, path
        for directory in BACKUP_DIRS:
            base = self.data_dir / directory
            if not base.is_dir():
                continue
            pattern = f"{task_id}.json" if task_id and directory == "tasks" else "*"
            for path in sorted(base.glob(pattern)):
                if path.is_file():
                    yield path.relative_to(self.data_dir).as_posix(), path
                    continue
                if not path.is_dir():
                    continue
                # 指定任务时只进入该任务的目录；否则备份全部任务的产物
                if task_id and path.name != task_id:
                    continue
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        yield child.relative_to(self.data_dir).as_posix(), child

    def _is_synced(self, key: str, path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return True
        if stat.st_size > MAX_BACKUP_FILE_BYTES:
            return True
        with self._lock:
            return self._synced.get(key) == (stat.st_size, stat.st_mtime)

    def _mark_synced(self, key: str, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        with self._lock:
            self._synced[key] = (stat.st_size, stat.st_mtime)

    def forget(self, key: str) -> None:
        """文件被删除/清理后忘掉它的同步记录（供清理任务调用）。"""
        with self._lock:
            self._synced.pop(key, None)


class BackupScheduler:
    """后台定时备份（daemon 线程，随服务关闭而停止）。"""

    def __init__(self, service: BackupService, interval_seconds: int) -> None:
        self.service = service
        self.interval_seconds = max(30, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.service.storage.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="backup", daemon=True)
        self._thread.start()
        logger.info("backup_scheduler_started interval_seconds=%s", self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.service.backup_all()
            except Exception:  # noqa: BLE001 - 备份线程不允许因异常退出
                logger.warning("backup_cycle_failed", exc_info=True)
