from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


CURRENT_SCHEMA_VERSION = 3


class TaskStatus(StrEnum):
    PENDING = "pending"
    VALIDATING = "validating"
    CHECKING_SUBTITLE = "checking_subtitle"
    DOWNLOADING_AUDIO = "downloading_audio"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    EXPORTING = "exporting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: 终态：到这三个状态就不会再变化（阶段 3D 新增 `cancelled`）。
TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


def is_terminal(status: TaskStatus | str) -> bool:
    return TaskStatus(status) in TERMINAL_STATUSES


class MediaType(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    PLATFORM_URL = "platform_url"


class Platform(StrEnum):
    LOCAL = "local"
    BILIBILI = "bilibili"


class ExtractMethod(StrEnum):
    SUBTITLE = "subtitle"
    SPEECH_TO_TEXT = "speech_to_text"


class SubtitleKind(StrEnum):
    CC = "cc"
    AI = "ai"


class ProcessingMethod(StrEnum):
    """真实处理方式。取值与平台耦合，新增平台时在此追加。"""

    SPEECH_TO_TEXT = "speech_to_text"  # 阶段 1 历史取值，保持兼容
    SPEECH_TO_TEXT_LOCAL = "speech_to_text_local"
    SPEECH_TO_TEXT_REMOTE = "speech_to_text_remote"
    SUBTITLE_BILIBILI_CC = "subtitle_bilibili_cc"
    SUBTITLE_BILIBILI_AI = "subtitle_bilibili_ai"


PROCESSING_METHOD_LABELS: dict[ProcessingMethod, str] = {
    ProcessingMethod.SPEECH_TO_TEXT: "语音转写",
    ProcessingMethod.SPEECH_TO_TEXT_LOCAL: "语音转写",
    ProcessingMethod.SPEECH_TO_TEXT_REMOTE: "语音转写",
    ProcessingMethod.SUBTITLE_BILIBILI_CC: "字幕提取（人工字幕）",
    ProcessingMethod.SUBTITLE_BILIBILI_AI: "字幕提取（AI 字幕）",
}

SOURCE_TYPE_LABELS: dict[SourceType, str] = {
    SourceType.LOCAL_FILE: "本地文件",
    SourceType.PLATFORM_URL: "Bilibili",
}


def processing_method_label(method: ProcessingMethod | str) -> str:
    try:
        return PROCESSING_METHOD_LABELS[ProcessingMethod(method)]
    except (ValueError, KeyError):
        return "语音转写"


def source_type_label(source_type: SourceType | str) -> str:
    try:
        return SOURCE_TYPE_LABELS[SourceType(source_type)]
    except (ValueError, KeyError):
        return "本地文件"


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

    schema_version: int = CURRENT_SCHEMA_VERSION
    task_id: str
    status: TaskStatus
    source_type: SourceType = SourceType.LOCAL_FILE
    platform: Platform = Platform.LOCAL
    source_url: str | None = None
    resolved_url: str | None = None
    original_filename: str
    stored_filename: str
    media_type: MediaType
    content_type: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    media_duration_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    progress_stage: TaskStatus
    processing_method: ProcessingMethod = ProcessingMethod.SPEECH_TO_TEXT
    extract_method: ExtractMethod | None = None
    subtitle_kind: SubtitleKind | None = None
    downloaded_bytes: int | None = Field(default=None, ge=0)
    # 阶段 3（schema v3）：转写过程的可见性与可恢复性
    stage_message: str | None = None
    transcribed_seconds: float | None = Field(default=None, ge=0)
    segment_count: int = Field(default=0, ge=0)
    resumed_count: int = Field(default=0, ge=0)
    partial_result_available: bool = False
    error: TaskError | None = None
    artifacts: TaskArtifacts = Field(default_factory=TaskArtifacts)


class TaskCreatedResponse(BaseModel):
    task_id: str
    status: TaskStatus


class CreateTaskFromUrlRequest(BaseModel):
    # 不用 min_length 拦截空串：交给平台校验层给出更明确的中文提示。
    url: str = Field(max_length=2048)


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stage_message: str
    source_type: SourceType
    platform: Platform
    source_url: str | None = None
    extract_method: ExtractMethod | None = None
    subtitle_kind: SubtitleKind | None = None
    processing_method_label: str
    created_at: datetime
    updated_at: datetime
    elapsed_seconds: float = Field(ge=0)
    media_duration_seconds: float | None = Field(default=None, ge=0)
    estimated_remaining_seconds: float | None = Field(default=None, ge=0)
    segment_count: int = Field(default=0, ge=0)
    transcribed_seconds: float | None = Field(default=None, ge=0)
    progress_percent: float = Field(default=0.0, ge=0, le=100)
    partial_result_available: bool = False
    #: 用户是否还能取消（终态任务不可取消）
    cancellable: bool = False
    error: TaskError | None
    artifacts: dict[str, str | None]


class SegmentItem(BaseModel):
    """增量拉取接口返回的单个片段。"""

    index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class TaskSegmentsResponse(BaseModel):
    task_id: str
    status: TaskStatus
    #: true 表示这是尚未完成的部分结果（任务未成功）；成功完成后为 false。
    partial: bool
    total: int = Field(ge=0)
    next_after: int = Field(ge=0)
    has_more: bool
    segments: list[SegmentItem]


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class TranscriptResult(BaseModel):
    schema_version: int = 2
    task_id: str
    source_type: SourceType = SourceType.LOCAL_FILE
    platform: Platform = Platform.LOCAL
    source_url: str | None = None
    original_filename: str
    media_type: MediaType
    processing_method: ProcessingMethod = ProcessingMethod.SPEECH_TO_TEXT
    subtitle_kind: SubtitleKind | None = None
    language: str | None = None
    duration_seconds: float | None = None
    text: str
    segments: list[TranscriptSegment]
    generated_at: datetime


STAGE_MESSAGES: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "任务已创建",
    TaskStatus.VALIDATING: "正在校验文件",
    TaskStatus.CHECKING_SUBTITLE: "正在检查视频字幕",
    TaskStatus.DOWNLOADING_AUDIO: "未发现可用字幕，正在下载音频",
    TaskStatus.EXTRACTING_AUDIO: "正在提取音频",
    TaskStatus.TRANSCRIBING: "正在生成逐字稿",
    TaskStatus.EXPORTING: "正在生成文件",
    TaskStatus.SUCCEEDED: "逐字稿已生成",
    TaskStatus.FAILED: "处理失败",
    TaskStatus.CANCELLED: "任务已取消",
}
