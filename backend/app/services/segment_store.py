from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from backend.app.schemas.task import TranscriptSegment


logger = logging.getLogger(__name__)

SEGMENTS_FILE_NAME = "segments.jsonl"


@dataclass(frozen=True)
class StoredSegment:
    index: int
    start: float
    end: float
    text: str

    def to_segment(self) -> TranscriptSegment:
        return TranscriptSegment(start=self.start, end=self.end, text=self.text)


class SegmentStore:
    """转写片段的 append-only 存储。

    每个任务一个 ``segments.jsonl``，一行一个片段。三个关键设计：

    1. **追加写（O(1)）**：5 小时内容可能有数千个片段，整体重写会变成 O(n²) 写放大；
    2. **每行 flush**：进程被强杀时，已完成片段仍在磁盘上，断点续写才有意义；
    3. **容错读取**：解析失败的最后一行为“写了一半”的残留，直接丢弃，
       不让半个片段毁掉整个任务。

    线程安全：处理器运行在独立线程中，而接口线程会读取同一批文件，因此加锁串行化。
    """

    def __init__(self, outputs_dir: Path, *, max_segments: int = 200_000) -> None:
        self.outputs_dir = outputs_dir
        self.max_segments = max_segments
        self._lock = threading.RLock()
        self._capped_tasks: set[str] = set()

    def path_for(self, task_id: str) -> Path:
        return self.outputs_dir / task_id / SEGMENTS_FILE_NAME

    # ------------------------------------------------------------------ 写入

    def append(self, task_id: str, segment: TranscriptSegment) -> int | None:
        """追加一个片段，返回它的序号。超过上限时返回 ``None`` 并只记录一次告警。"""
        with self._lock:
            current = self.count(task_id)
            if current >= self.max_segments:
                if task_id not in self._capped_tasks:
                    self._capped_tasks.add(task_id)
                    logger.warning(
                        "segment_limit_reached task_id=%s limit=%s", task_id, self.max_segments
                    )
                return None
            path = self.path_for(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "index": current,
                "start": max(0.0, float(segment.start)),
                "end": max(0.0, float(segment.end)),
                "text": segment.text,
            }
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    handle.flush()
            except OSError:
                logger.warning("segment_append_failed task_id=%s", task_id, exc_info=True)
                return None
            return current

    def reset(self, task_id: str) -> None:
        with self._lock:
            self.path_for(task_id).unlink(missing_ok=True)
            self._capped_tasks.discard(task_id)

    # ------------------------------------------------------------------ 读取

    def read(self, task_id: str, *, after: int = 0, limit: int | None = None) -> list[StoredSegment]:
        """读取序号 >= ``after`` 的片段。``after`` 之前的行只计数、不做 JSON 解析。"""
        with self._lock:
            found: list[StoredSegment] = []
            for line_number, stored in enumerate(self._iter(task_id)):
                if line_number < after:
                    continue
                found.append(stored)
                if limit is not None and len(found) >= limit:
                    break
            return found

    def read_all(self, task_id: str) -> list[StoredSegment]:
        return self.read(task_id, after=0, limit=None)

    def count(self, task_id: str) -> int:
        with self._lock:
            return sum(1 for _ in self._iter(task_id))

    def last_end(self, task_id: str) -> float:
        """断点：已落盘片段的最大结束时间。"""
        with self._lock:
            latest = 0.0
            for stored in self._iter(task_id):
                latest = max(latest, stored.end)
            return latest

    def has_content(self, task_id: str) -> bool:
        return self.count(task_id) > 0

    # ------------------------------------------------------------------ 内部

    def _iter(self, task_id: str):
        path = self.path_for(task_id)
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                        yield StoredSegment(
                            index=int(payload["index"]),
                            start=float(payload["start"]),
                            end=float(payload["end"]),
                            text=str(payload["text"]),
                        )
                    except (ValueError, KeyError, TypeError):
                        # 只可能是最后一行被截断：丢弃它，其余片段照常可用。
                        logger.info("segment_line_skipped task_id=%s", task_id)
                        continue
        except OSError:
            logger.warning("segment_read_failed task_id=%s", task_id, exc_info=True)
            return
