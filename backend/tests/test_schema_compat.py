from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.schemas.task import CURRENT_SCHEMA_VERSION, MediaType, Platform, SourceType, TaskStatus
from backend.app.services.task_service import TaskService


TASK_ID = "13f154b7-feb7-412f-b9c1-3ee1ef20cf87"

# 阶段 1 真实任务记录的结构（schema_version=1，缺少 v2 的新字段）。
V1_RECORD = {
    "schema_version": 1,
    "task_id": TASK_ID,
    "status": "succeeded",
    "source_type": "local_file",
    "original_filename": "20260829_120503.m4a",
    "stored_filename": "source.m4a",
    "media_type": "audio",
    "content_type": "audio/x-m4a",
    "size_bytes": 18549305,
    "created_at": "2026-09-22T13:49:53.021857+08:00",
    "updated_at": "2026-09-22T13:56:42.107273+08:00",
    "progress_stage": "succeeded",
    "processing_method": "speech_to_text",
    "error": None,
    "artifacts": {"markdown": None, "txt": None, "result": None},
}


def v1_result_payload() -> dict:
    return {
        "schema_version": 1,
        "task_id": TASK_ID,
        "original_filename": "20260829_120503.m4a",
        "media_type": "audio",
        "processing_method": "speech_to_text",
        "language": "zh",
        "duration_seconds": 991.7,
        "text": "毕业之后呢",
        "segments": [{"start": 0.4, "end": 5.4, "text": "毕业之后呢"}],
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def test_v1_record_is_readable_and_migrated_on_next_write(tmp_path: Path):
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / f"{TASK_ID}.json").write_text(
        json.dumps(V1_RECORD, ensure_ascii=False), encoding="utf-8"
    )
    service = TaskService(tasks_dir)

    record = service.get(TASK_ID)
    assert record.status == TaskStatus.SUCCEEDED
    assert record.source_type == SourceType.LOCAL_FILE
    assert record.platform == Platform.LOCAL
    assert record.extract_method is None
    assert record.subtitle_kind is None
    assert record.media_duration_seconds is None
    # 只读不写时，磁盘上仍是旧版本
    assert json.loads((tasks_dir / f"{TASK_ID}.json").read_text(encoding="utf-8"))[
        "schema_version"
    ] == 1

    service.set_original_filename(TASK_ID, "改名后的音频")
    raw = json.loads((tasks_dir / f"{TASK_ID}.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == CURRENT_SCHEMA_VERSION
    # v1 → v3 迁移时补齐新字段，不需要人工干预
    assert raw["segment_count"] == 0
    assert raw["partial_result_available"] is False
    assert raw["stage_message"] is None
    assert raw["original_filename"] == "改名后的音频"
    assert raw["stored_filename"] == "source.m4a"
    assert raw["size_bytes"] == 18549305


def test_v1_task_can_still_be_queried_downloaded_and_read(settings):
    settings.ensure_directories()
    output_dir = settings.outputs_dir / TASK_ID
    output_dir.mkdir(parents=True)
    markdown = output_dir / "transcript.md"
    markdown.write_text("# 旧任务\n\n- 处理方式：语音转写\n", encoding="utf-8")
    txt = output_dir / "transcript.txt"
    txt.write_text("[00:00:00] 毕业之后呢\n", encoding="utf-8")
    result = output_dir / "result.json"
    result.write_text(json.dumps(v1_result_payload(), ensure_ascii=False), encoding="utf-8")

    record = dict(V1_RECORD)
    record["artifacts"] = {
        "markdown": str(markdown),
        "txt": str(txt),
        "result": str(result),
    }
    (settings.tasks_dir / f"{TASK_ID}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )

    app = create_app(settings)
    with TestClient(app) as client:
        status_response = client.get(f"/api/v1/tasks/{TASK_ID}")
        markdown_response = client.get(f"/api/v1/tasks/{TASK_ID}/download/markdown")
        result_response = client.get(f"/api/v1/tasks/{TASK_ID}/result")

    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "succeeded"
    assert body["source_type"] == "local_file"
    assert body["platform"] == "local"
    assert body["processing_method_label"] == "语音转写"
    assert body["extract_method"] is None
    assert body["estimated_remaining_seconds"] is None
    assert body["artifacts"]["markdown"] == f"/api/v1/tasks/{TASK_ID}/download/markdown"
    assert markdown_response.status_code == 200
    assert result_response.status_code == 200
    assert result_response.json()["text"] == "毕业之后呢"
    assert result_response.json()["source_type"] == "local_file"


def test_v2_platform_task_records_new_fields(settings):
    settings.ensure_directories()
    service = TaskService(settings.tasks_dir)
    task_id = service.new_task_id()
    service.create(
        task_id=task_id,
        original_filename="BV1BqhB6nEdN",
        stored_filename="",
        media_type=MediaType.VIDEO,
        content_type=None,
        size_bytes=0,
        source_type=SourceType.PLATFORM_URL,
        platform=Platform.BILIBILI,
        source_url="https://www.bilibili.com/video/BV1BqhB6nEdN",
        resolved_url="https://www.bilibili.com/video/BV1BqhB6nEdN",
    )
    raw = json.loads((settings.tasks_dir / f"{task_id}.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == CURRENT_SCHEMA_VERSION
    assert raw["source_type"] == "platform_url"
    assert raw["platform"] == "bilibili"
    assert raw["resolved_url"] == "https://www.bilibili.com/video/BV1BqhB6nEdN"
