from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    VALIDATING = "validating"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    EXPORTING = "exporting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MediaType(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class TaskError(BaseModel):
    code: str
    message: str
    internal_type: str | None = None
    failed_stage: TaskStatus | None = None


class TaskArtifacts(BaseModel):
    markdown: str | None = None
    txt: str | None = None
    result: str | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    schema_version: int = 1
    task_id: str
    status: TaskStatus
    source_type: str = "local_file"
    original_filename: str
    stored_filename: str
    media_type: MediaType
    content_type: str | None = None
    size_bytes: int = Field(ge=0)
    media_duration_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    progress_stage: TaskStatus
    processing_method: str = "speech_to_text"
    error: TaskError | None = None
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)


class TaskCreatedResponse(BaseModel):
    task_id: str
    status: TaskStatus


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stage_message: str
    created_at: datetime
    updated_at: datetime
    elapsed_seconds: float = Field(ge=0)
    media_duration_seconds: float | None = Field(default=None, ge=0)
    error: TaskError | None
    artifacts: dict[str, str | None]


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class TranscriptResult(BaseModel):
    schema_version: int = 1
    task_id: str
    original_filename: str
    media_type: MediaType
    processing_method: str = "speech_to_text"
    language: str | None = None
    duration_seconds: float | None = None
    text: str
    segments: list[TranscriptSegment]
    generated_at: datetime


STAGE_MESSAGES: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "任务已创建",
    TaskStatus.VALIDATING: "正在校验文件",
    TaskStatus.EXTRACTING_AUDIO: "正在提取音频",
    TaskStatus.TRANSCRIBING: "正在生成逐字稿",
    TaskStatus.EXPORTING: "正在生成文件",
    TaskStatus.SUCCEEDED: "逐字稿已生成",
    TaskStatus.FAILED: "处理失败",
}
