from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Callable

from backend.app.core.errors import AppError
from backend.app.core.filenames import sanitize_filename
from backend.app.core.messages import (
    RESUMING_MESSAGE,
    TRANSCRIPTION_STAGE_MESSAGES,
    file_too_long_message,
    format_duration,
    incomplete_audio_message,
    transcribing_progress_message,
)
from backend.app.schemas.task import (
    ExtractMethod,
    MediaType,
    ProcessingMethod,
    SourceType,
    SubtitleKind,
    TaskArtifacts,
    TaskStatus,
    TranscriptResult,
    TranscriptSegment,
)
from backend.app.services.download_service import DownloadService
from backend.app.services.export_service import ExportService
from backend.app.services.media_service import MediaService
from backend.app.services.segment_store import SegmentStore
from backend.app.services.subtitle_service import SubtitleService
from backend.app.services.task_service import TaskService
from backend.app.services.transcription_service import TranscriptionService


logger = logging.getLogger(__name__)

# 实际音频短于声明时长的这个比例时，判定为不完整（防意外截断的通用安全网）。
MIN_AUDIO_COVERAGE_RATIO = 0.9

# 断点续写用的临时音频文件名（放在该任务的中间音频目录下）
RESUME_AUDIO_NAME = "resume.wav"

# “没有人声”类错误的用户文案：同一错误码在不同来源下说人话。
SPEECH_ERROR_MESSAGES: dict[str, dict[SourceType, str]] = {
    "SILENT_AUDIO": {
        SourceType.LOCAL_FILE: (
            "未在文件中检测到人声。请确认文件是否正确，或更换包含人声的文件后重试。"
        ),
        SourceType.PLATFORM_URL: "未在该视频中检测到人声。请确认视频是否包含人声内容。",
    },
    "EMPTY_TRANSCRIPT": {
        SourceType.LOCAL_FILE: "未能从文件中提取到语音内容。请确认文件是否正确，或更换文件后重试。",
        SourceType.PLATFORM_URL: "未能从该视频中提取到语音内容。请确认视频内容后重试。",
    },
}


def _rethrow_with_source_wording(exc: AppError, source_type: SourceType) -> None:
    """把服务层的通用错误换成针对当前来源的具体说法，然后重新抛出。"""
    message = SPEECH_ERROR_MESSAGES.get(exc.code, {}).get(source_type)
    if message:
        raise AppError(exc.code, message) from exc


@dataclass(frozen=True)
class _ResumeContext:
    """续写准备结果：已落盘片段、时间偏移、实际要转写的音频、是否真在续写。"""

    existing_segments: list[TranscriptSegment]
    start_offset: float
    audio_path: Path
    resumed: bool


class _TranscriptionProgress:
    """转写过程的“即时落盘 + 节流上报”（阶段 3）。

    - **片段逐条落盘**：每产出一个片段就追加进 ``segments.jsonl``（追加写 O(1)），
      进程被强杀时已完成的内容仍在磁盘上，断点续写才有意义；
    - **进度节流写回**：任务记录是整体原子替换写入（带 fsync），数千次重写会形成
      明显的写放大，因此进度按时间节流（默认 1 秒）写回；阶段切换、转写结束与失败
      这些关键时刻由调用方强制写回，保证断点不滞后。
    """

    def __init__(
        self,
        *,
        task_service: TaskService,
        segment_store: SegmentStore,
        task_id: str,
        interval_seconds: float = 1.0,
        existing_segments: list[TranscriptSegment] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        dedupe: bool | None = None,
    ) -> None:
        self.task_service = task_service
        self.segment_store = segment_store
        self.task_id = task_id
        self.interval_seconds = max(0.0, interval_seconds)
        self.is_cancelled = is_cancelled
        # 已落盘片段（续写时为上次中断前的内容）+ 本次新产出的片段，
        # 两者拼在一起才是完整结果；按时间戳去重，不做文本比对。
        self.collected: list[TranscriptSegment] = list(existing_segments or [])
        # 检查点 = 已落盘片段的最大结束时间；续写时它就是去重边界。
        self.checkpoint = max((s.end for s in self.collected), default=0.0)
        self.resumed = bool(self.collected)
        # 是否需要去重：续写时需要；分窗转写即使全新任务也需要（相邻窗口有重叠区）。
        self.dedupe = self.resumed if dedupe is None else dedupe
        self.segment_count = len(self.collected)
        self.transcribed_seconds = self.checkpoint
        # 首个片段立即写回，用户不必等到第一个节流周期结束才看到进度。
        self._awaiting_first_segment = True
        self._last_persist_at = perf_counter()

    def on_stage(self, code: str) -> None:
        if self.resumed and code == "transcribing":
            message = RESUMING_MESSAGE
        else:
            message = TRANSCRIPTION_STAGE_MESSAGES.get(code)
        if message is not None:
            self.task_service.set_stage_message(self.task_id, message)

    def on_segment(self, segment: TranscriptSegment) -> None:
        # 取消检查点（阶段 3D）：每个片段产出时都看一眼是否已被取消，
        # 是则立即停止解码；已落盘的片段保留。
        if self.is_cancelled is not None and self.is_cancelled():
            raise AppError("TASK_CANCELLED", "任务已取消")
        # 重叠区：断点前的片段、以及相邻窗口的重叠部分已经落盘过，直接丢弃
        # （按时间戳去重，不做文本比对）。分窗转写即使在“全新任务”时也需要去重，
        # 因此这里由 dedupe 控制，而不是只看是否续写。
        if self.dedupe and segment.end <= self.checkpoint:
            return
        self.segment_store.append(self.task_id, segment)
        self.collected.append(segment)
        self.segment_count = len(self.collected)
        self.checkpoint = max(self.checkpoint, float(segment.end))
        self.transcribed_seconds = max(self.transcribed_seconds, float(segment.end))
        throttled = perf_counter() - self._last_persist_at >= self.interval_seconds
        if self._awaiting_first_segment or throttled:
            self._awaiting_first_segment = False
            self.persist(stage_message=transcribing_progress_message(self.segment_count))

    def persist(self, *, stage_message: str | None = None) -> None:
        self.task_service.set_progress(
            self.task_id,
            segment_count=self.segment_count,
            transcribed_seconds=self.transcribed_seconds,
            stage_message=stage_message,
        )
        self._last_persist_at = perf_counter()


class TaskProcessor:
    def __init__(
        self,
        *,
        task_service: TaskService,
        media_service: MediaService,
        transcription_service: TranscriptionService,
        export_service: ExportService,
        download_service: DownloadService,
        subtitle_service: SubtitleService,
        platform_media_service=None,
        segment_store: SegmentStore,
        backup_service=None,
        uploads_dir: Path,
        audio_dir: Path,
        downloads_dir: Path,
        max_media_seconds: float = 180 * 60,
        max_media_minutes: int = 180,
        max_workers: int = 1,
        progress_persist_interval_seconds: float = 1.0,
        resume_overlap_seconds: float = 2.0,
        transcribe_window_seconds: float = 0.0,
        transcribe_window_overlap_seconds: float = 5.0,
    ) -> None:
        self.task_service = task_service
        self.media_service = media_service
        self.transcription_service = transcription_service
        self.export_service = export_service
        self.download_service = download_service
        self.subtitle_service = subtitle_service
        # 链接链路的实际实现：B 站 API 或 yt-dlp（两者接口一致，处理器不关心是哪一种）
        self.platform_media_service = platform_media_service or download_service
        self.uploads_dir = uploads_dir
        self.audio_dir = audio_dir
        self.downloads_dir = downloads_dir
        self.max_media_seconds = max_media_seconds
        self.max_media_minutes = max_media_minutes
        self.segment_store = segment_store
        self.backup_service = backup_service
        self.progress_persist_interval_seconds = progress_persist_interval_seconds
        self.resume_overlap_seconds = max(0.0, resume_overlap_seconds)
        # 分窗转写（阶段 3.5）：默认关闭，由应用层按配置开启（见 main.create_app）。
        self.transcribe_window_seconds = max(0.0, float(transcribe_window_seconds))
        self.transcribe_window_overlap_seconds = max(
            0.0, float(transcribe_window_overlap_seconds)
        )
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="transcript")

    def submit(self, task_id: str) -> None:
        self.executor.submit(self.process, task_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    # ------------------------------------------------------------------ 调度

    def process(self, task_id: str) -> None:
        task_started = perf_counter()
        try:
            record = self.task_service.get(task_id)
            if record.source_type == SourceType.PLATFORM_URL:
                self._process_platform(task_id, record, task_started)
            else:
                self._process_local(task_id, record, task_started)
            logger.info(
                "task_succeeded task_id=%s duration_seconds=%.3f",
                task_id,
                perf_counter() - task_started,
            )
        except AppError as exc:
            logger.warning(
                "task_failed task_id=%s code=%s duration_seconds=%.3f",
                task_id,
                exc.code,
                perf_counter() - task_started,
            )
            self._mark_partial_result(task_id)
            self.task_service.fail(
                task_id,
                code=exc.code,
                message=exc.message,
                internal_type=type(exc).__name__,
            )
            self._backup_task(task_id)
        except Exception as exc:
            logger.exception(
                "task_failed_unexpected task_id=%s type=%s duration_seconds=%.3f",
                task_id,
                type(exc).__name__,
                perf_counter() - task_started,
            )
            self._mark_partial_result(task_id)
            self.task_service.fail(
                task_id,
                code="INTERNAL_PROCESSING_ERROR",
                message="处理失败，请重试。原始文件不会被修改",
                internal_type=type(exc).__name__,
            )
            self._backup_task(task_id)

    # ------------------------------------------------------- 路径一：本地文件

    def _process_local(self, task_id: str, record, task_started: float) -> None:
        stage_started = perf_counter()
        record = self.task_service.transition(task_id, TaskStatus.VALIDATING)
        source = self.uploads_dir / task_id / record.stored_filename
        if not source.is_file():
            raise AppError("UPLOAD_NOT_FOUND", "上传文件不存在")
        media_info = self.media_service.inspect(source, record.media_type)
        self._ensure_duration_within_limit(media_info.duration_seconds)
        self.task_service.set_media_duration(task_id, media_info.duration_seconds)
        self._log_stage(task_id, TaskStatus.VALIDATING, stage_started)

        audio_path = source
        if record.media_type == MediaType.VIDEO:
            if self._use_windows(media_info.duration_seconds):
                # 长视频：不预先抽取整段 WAV（6 小时约 690 MB），转写时按窗口切片。
                audio_path = source
            else:
                stage_started = perf_counter()
                self.task_service.transition(task_id, TaskStatus.EXTRACTING_AUDIO)
                audio_path = self.audio_dir / task_id / "audio.wav"
                self.media_service.extract_audio(source, audio_path)
                self._log_stage(task_id, TaskStatus.EXTRACTING_AUDIO, stage_started)

        self._transcribe_and_export(
            task_id,
            audio_path=audio_path,
            fallback_duration=media_info.duration_seconds,
            processing_method=ProcessingMethod.SPEECH_TO_TEXT_LOCAL,
            source_type=record.source_type,
            task_started=task_started,
        )

    # --------------------------------------------------------- 路径二：链接

    def _process_platform(self, task_id: str, record, task_started: float) -> None:
        url = record.resolved_url or record.source_url
        if not url:
            raise AppError("INVALID_SOURCE_URL", "链接格式不正确，请粘贴完整的 B 站视频链接")

        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.CHECKING_SUBTITLE)
        meta = self.platform_media_service.probe(url)
        self.task_service.set_media_duration(task_id, meta.duration_seconds)
        if meta.title:
            self.task_service.set_original_filename(
                task_id,
                sanitize_filename(meta.title, fallback=meta.video_id or task_id),
            )
        self._log_stage(task_id, TaskStatus.CHECKING_SUBTITLE, stage_started)

        selection = self.subtitle_service.select(meta.subtitles, meta.automatic_captions)
        if selection is not None:
            self._finish_with_subtitle(task_id, selection, meta, task_started)
            return

        # 无可用字幕：只下载音频轨，然后复用阶段 1 已验证的转写链路。
        stage_started = perf_counter()
        self.task_service.transition(task_id, TaskStatus.DOWNLOADING_AUDIO)
        downloaded = self.platform_media_service.download_audio(
            url, self.downloads_dir / task_id, task_id
        )
        self.task_service.set_downloaded_bytes(task_id, downloaded.size_bytes)
        if self._use_windows(meta.duration_seconds):
            # 长内容：保留压缩音频，转写时按窗口切片，不生成整段 WAV。
            media_path = downloaded.path
        else:
            media_path = self.audio_dir / task_id / "audio.wav"
            self.media_service.extract_audio(downloaded.path, media_path)
        self._ensure_audio_complete(media_path, meta.duration_seconds)
        self._log_stage(task_id, TaskStatus.DOWNLOADING_AUDIO, stage_started)

        self._transcribe_and_export(
            task_id,
            audio_path=media_path,
            fallback_duration=meta.duration_seconds,
            processing_method=ProcessingMethod.SPEECH_TO_TEXT_REMOTE,
            source_type=record.source_type,
            task_started=task_started,
        )

    def _finish_with_subtitle(self, task_id: str, selection, meta, task_started: float) -> None:
        processing_method = (
            ProcessingMethod.SUBTITLE_BILIBILI_CC
            if selection.kind == SubtitleKind.CC
            else ProcessingMethod.SUBTITLE_BILIBILI_AI
        )
        record = self.task_service.set_extraction(
            task_id,
            processing_method=processing_method,
            extract_method=ExtractMethod.SUBTITLE,
            subtitle_kind=selection.kind,
        )
        result = TranscriptResult(
            task_id=task_id,
            source_type=record.source_type,
            platform=record.platform,
            source_url=record.source_url,
            original_filename=record.original_filename,
            media_type=record.media_type,
            processing_method=processing_method,
            subtitle_kind=selection.kind,
            language=selection.language,
            duration_seconds=meta.duration_seconds,
            text=selection.text,
            segments=selection.segments,
            generated_at=datetime.now().astimezone(),
        )
        self._export_and_succeed(task_id, result, task_started)

    # --------------------------------------------------------------- 共用尾部

    def _use_windows(self, duration: float | None) -> bool:
        """内容时长超过窗口大小时才分窗（阶段 3.5）。

        时长未知时不冒险：退回整段转写。短内容（时长 <= 窗口）保持原路径，
        行为与阶段 1–3 完全一致。
        """
        return (
            self.transcribe_window_seconds > 0
            and duration is not None
            and duration > self.transcribe_window_seconds
        )

    def _transcribe_and_export(
        self,
        task_id: str,
        *,
        audio_path: Path,
        fallback_duration: float | None,
        processing_method: ProcessingMethod,
        source_type: SourceType,
        task_started: float,
    ) -> None:
        stage_started = perf_counter()
        record = self.task_service.transition(task_id, TaskStatus.TRANSCRIBING)
        duration = record.media_duration_seconds or fallback_duration

        try:
            if self._use_windows(duration):
                progress, language, result_duration = self._transcribe_in_windows(
                    task_id, audio_path, duration
                )
            else:
                progress, language, result_duration = self._transcribe_single_pass(
                    task_id, audio_path, duration
                )
        except AppError as exc:
            _rethrow_with_source_wording(exc, source_type)
            raise
        progress.persist()
        self._log_stage(task_id, TaskStatus.TRANSCRIBING, stage_started)

        record = self.task_service.set_extraction(
            task_id,
            processing_method=processing_method,
            extract_method=ExtractMethod.SPEECH_TO_TEXT,
        )
        # 结果以“落盘片段 + 本次新产出片段”为准（续写/分窗时两者拼接），
        # 而不是本次转写服务的返回值，否则会丢掉中断前的内容。
        segments = progress.collected
        result = TranscriptResult(
            task_id=task_id,
            source_type=record.source_type,
            platform=record.platform,
            source_url=record.source_url,
            original_filename=record.original_filename,
            media_type=record.media_type,
            processing_method=processing_method,
            language=language,
            duration_seconds=result_duration or fallback_duration,
            text="\n".join(segment.text for segment in segments).strip(),
            segments=segments,
            generated_at=datetime.now().astimezone(),
        )
        self._export_and_succeed(task_id, result, task_started)

    def _transcribe_single_pass(
        self, task_id: str, audio_path: Path, duration: float | None
    ) -> tuple[_TranscriptionProgress, str | None, float | None]:
        """阶段 1–3 的原有路径：整段一次转写，支持断点续写。"""
        resume = self._resume_context(task_id, audio_path, duration=duration)
        progress = _TranscriptionProgress(
            task_service=self.task_service,
            segment_store=self.segment_store,
            task_id=task_id,
            interval_seconds=self.progress_persist_interval_seconds,
            existing_segments=resume.existing_segments,
            is_cancelled=lambda: self.task_service.is_cancelled(task_id),
        )
        try:
            transcription = self.transcription_service.transcribe(
                resume.audio_path,
                on_stage=progress.on_stage,
                on_segment=progress.on_segment,
                start_offset=resume.start_offset,
                allow_empty_result=resume.resumed,
            )
        except AppError:
            # 失败也要把已完成的进度写回：部分结果不丢，用户能看到已经转出来的内容。
            progress.persist()
            raise
        return progress, transcription.language, transcription.duration_seconds

    def _transcribe_in_windows(
        self, task_id: str, audio_path: Path, duration: float | None
    ) -> tuple[_TranscriptionProgress, str | None, float | None]:
        """长内容分窗转写（阶段 3.5）。

        把音频按固定时间窗切片逐段转写，而不是一次性解码整段：

        - 内存占用与总时长解耦（6 小时内容不再需要约 1.3 GB 的解码数组）；
        - 每个窗口独立完成即落盘，中断最多丢一个窗口；
        - 窗口之间重叠若干秒并按时间戳去重，避免切在词中间丢字；
        - 从“检查点所在窗口”开始，已完成的前面窗口不再重跑。
        """
        if not duration or duration <= 0:
            # 理论上不会走到这里（_use_windows 已要求时长已知）；兜底整段转写。
            return self._transcribe_single_pass(task_id, audio_path, duration)

        existing = [stored.to_segment() for stored in self.segment_store.read_all(task_id)]
        progress = _TranscriptionProgress(
            task_service=self.task_service,
            segment_store=self.segment_store,
            task_id=task_id,
            interval_seconds=self.progress_persist_interval_seconds,
            existing_segments=existing,
            is_cancelled=lambda: self.task_service.is_cancelled(task_id),
            # 全新任务同样需要按时间戳去重（相邻窗口有重叠区）。
            dedupe=True,
        )
        if progress.resumed:
            self.task_service.increment_resumed_count(task_id)
            self.task_service.set_stage_message(task_id, RESUMING_MESSAGE)

        window = max(1.0, float(self.transcribe_window_seconds))
        overlap = max(0.0, float(self.transcribe_window_overlap_seconds))
        index = int(progress.checkpoint // window) if progress.checkpoint > 0 else 0
        language: str | None = None
        speech_seconds = 0.0
        vad_total_seconds = 0.0
        has_vad_info = False

        while index * window < duration and progress.checkpoint < duration - 0.5:
            nominal_start = index * window
            slice_start = max(0.0, nominal_start - (overlap if index > 0 else 0.0))
            slice_end = min(duration, nominal_start + window + overlap)
            chunk_path = self.audio_dir / task_id / f"window-{index:04d}.wav"
            self.media_service.slice_audio(
                audio_path,
                chunk_path,
                slice_start,
                duration_seconds=max(0.1, slice_end - slice_start),
            )
            try:
                transcription = self.transcription_service.transcribe(
                    chunk_path,
                    on_stage=progress.on_stage,
                    on_segment=progress.on_segment,
                    start_offset=slice_start,
                    # 单个窗口全静音是正常的，不能据此判定整段没有人声。
                    allow_empty_result=True,
                )
            except AppError:
                progress.persist()
                raise
            finally:
                # 每个窗口用完即删，避免长内容在磁盘上堆积几十份音频。
                chunk_path.unlink(missing_ok=True)

            language = transcription.language or language
            if transcription.duration_after_vad is not None:
                has_vad_info = True
                speech_seconds += float(transcription.duration_after_vad)
                vad_total_seconds += float(transcription.duration_seconds or 0.0)
            # 静音窗口也要推进“已转写时长”，否则进度条会卡住不动。
            progress.transcribed_seconds = max(
                progress.transcribed_seconds, min(duration, nominal_start + window)
            )
            progress.persist(
                stage_message=transcribing_progress_message(progress.segment_count)
            )
            index += 1

        # 全局“没有人声”判定：单窗无法判定，把所有窗口的 VAD 结果累加后统一收口。
        if has_vad_info:
            TranscriptionService.ensure_speech_present(vad_total_seconds, speech_seconds)
        if progress.segment_count == 0 and not progress.resumed:
            raise AppError("EMPTY_TRANSCRIPT", "未能提取到语音内容")
        # 结果时长用整段声明时长，而不是最后一个窗口的切片长度。
        return progress, language, duration

    # ------------------------------------------------------------ 断点续写（3B）

    def _resume_context(
        self, task_id: str, audio_path: Path, *, duration: float | None
    ) -> _ResumeContext:
        """判断能不能接着上次的断点跑，并准备好续写用的音频。

        检查点 = 已落盘片段的最大结束时间（不额外维护检查点文件，避免两个数据源）。
        从 ``检查点 - 重叠秒数`` 处截取音频，重叠区逐片段丢弃，避免断点正好
        落在词中间导致开头丢字。
        """
        existing = [
            stored.to_segment() for stored in self.segment_store.read_all(task_id)
        ]
        checkpoint = max((segment.end for segment in existing), default=0.0)
        if checkpoint <= 0:
            return _ResumeContext(existing, 0.0, audio_path, False)

        start = max(0.0, checkpoint - self.resume_overlap_seconds)
        if duration and duration > 0:
            start = min(start, max(0.0, duration - self.resume_overlap_seconds))
        try:
            sliced = self.media_service.slice_audio(
                audio_path, self.audio_dir / task_id / RESUME_AUDIO_NAME, start
            )
        except AppError:
            # 续写准备失败不应让任务彻底卡死：退回完整重跑，并清掉旧片段避免重复。
            logger.warning(
                "resume_slice_failed task_id=%s checkpoint=%.3f", task_id, checkpoint, exc_info=True
            )
            self.segment_store.reset(task_id)
            return _ResumeContext([], 0.0, audio_path, False)

        self.task_service.increment_resumed_count(task_id)
        self.task_service.set_stage_message(task_id, RESUMING_MESSAGE)
        logger.info(
            "task_resuming task_id=%s checkpoint=%.3f start=%.3f segments=%d",
            task_id,
            checkpoint,
            start,
            len(existing),
        )
        return _ResumeContext(existing, start, sliced, True)

    def _export_and_succeed(self, task_id: str, result: TranscriptResult, task_started: float) -> None:
        stage_started = perf_counter()
        # 导出前的最后一个取消检查点：取消后的任务不应生成成功产物。
        if self.task_service.is_cancelled(task_id):
            raise AppError("TASK_CANCELLED", "任务已取消")
        self.task_service.transition(task_id, TaskStatus.EXPORTING)
        markdown_path, txt_path, result_path = self.export_service.export(result)
        artifacts = TaskArtifacts(
            markdown=str(markdown_path),
            txt=str(txt_path),
            result=str(result_path),
        )
        self.task_service.transition(task_id, TaskStatus.SUCCEEDED, artifacts=artifacts)
        self._log_stage(task_id, TaskStatus.EXPORTING, stage_started)
        self._backup_task(task_id)

    def _backup_task(self, task_id: str) -> None:
        """任务进入终态后立即备份：不依赖周期，结果不会因实例被回收而丢。"""
        if self.backup_service is None:
            return
        try:
            self.backup_service.backup_task(task_id)
        except Exception:  # noqa: BLE001 - 备份失败不能影响任务结果
            logger.warning("task_backup_failed task_id=%s", task_id, exc_info=True)

    def _ensure_duration_within_limit(self, duration_seconds: float | None) -> None:
        if duration_seconds is None or duration_seconds <= self.max_media_seconds:
            return
        raise AppError(
            "VIDEO_TOO_LONG",
            file_too_long_message(duration_seconds, self.max_media_minutes),
        )

    def _ensure_audio_complete(self, audio_path: Path, declared_seconds: float | None) -> None:
        """通用安全网：交叉校验实际音频时长与声明时长。

        防止任何形式的“静默截断”（会员预览片段、部分下载、平台异常）
        被当成完整转写向外呈现。
        """
        if not declared_seconds or declared_seconds <= 0:
            return
        actual = self.media_service.inspect(audio_path, MediaType.AUDIO).duration_seconds
        if actual is None:
            return
        if actual < declared_seconds * MIN_AUDIO_COVERAGE_RATIO:
            raise AppError(
                "DOWNLOAD_INCOMPLETE",
                incomplete_audio_message(actual, declared_seconds),
            )

    @staticmethod
    def _log_stage(task_id: str, stage: TaskStatus, started_at: float) -> None:
        logger.info(
            "task_stage_completed task_id=%s stage=%s duration_seconds=%.3f",
            task_id,
            stage.value,
            perf_counter() - started_at,
        )

    def sync_partial_progress(self, task_id: str) -> None:
        """把任务记录里的进度与磁盘上的片段对齐（磁盘是权威数据源）。

        两处会用到：任务失败时、用户取消时。进度写回是按秒节流的，
        这两个时刻记录可能落后于实际落盘量，而界面上的“已生成 N 段”
        必须与用户真能读到的内容一致。
        """
        stored = self.segment_store.read_all(task_id)
        if not stored:
            return
        self.task_service.mark_partial_result(task_id, available=True)
        self.task_service.set_progress(
            task_id,
            segment_count=len(stored),
            transcribed_seconds=max(segment.end for segment in stored),
        )

    def _mark_partial_result(self, task_id: str) -> None:
        """任务失败时，如果磁盘上已有片段，就标记“部分结果可用”。

        部分结果不生成 ``transcript.md`` / ``txt`` / ``result.json``：保持
        “成功产物才可下载”的既有语义，避免半成品被当成完整逐字稿。
        """
        try:
            self.sync_partial_progress(task_id)
        except Exception:  # noqa: BLE001 - 标记失败不应掩盖真正的失败原因
            logger.warning("partial_result_mark_failed task_id=%s", task_id, exc_info=True)
