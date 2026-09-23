from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.app.core.errors import AppError
from backend.app.schemas.task import TranscriptSegment


# 有效语音占比低于此值，即认为内容中没有人声（如纯音乐、环境音、静音文件）。
# 实测参考：一条以音乐为主的 122 秒视频，VAD 后仅剩 2.2 秒（1.8%）。
MIN_SPEECH_RATIO = 0.05
MIN_SPEECH_SECONDS = 0.5

DEFAULT_SILENT_AUDIO_MESSAGE = "未检测到人声内容"
DEFAULT_EMPTY_TRANSCRIPT_MESSAGE = "未能提取到语音内容"


@dataclass(frozen=True)
class Transcription:
    text: str
    segments: list[TranscriptSegment]
    language: str | None
    duration_seconds: float | None


class TranscriptionService:
    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._model_lock = threading.Lock()

    def check_runtime(self) -> None:
        if not self.model_name.strip() or not self.device.strip() or not self.compute_type.strip():
            raise AppError(
                "INVALID_TRANSCRIPTION_CONFIG",
                "语音识别模型配置不完整",
                status_code=503,
            )
        try:
            import faster_whisper  # noqa: F401
        except Exception as exc:
            raise AppError(
                "TRANSCRIPTION_RUNTIME_UNAVAILABLE",
                "语音识别运行环境不可用，请重新安装项目依赖",
                status_code=503,
            ) from exc

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from faster_whisper import WhisperModel

                        self._model = WhisperModel(
                            self.model_name,
                            device=self.device,
                            compute_type=self.compute_type,
                        )
                    except Exception as exc:
                        raise AppError(
                            "TRANSCRIPTION_MODEL_UNAVAILABLE",
                            "语音识别模型加载失败，请检查模型配置或网络",
                            status_code=503,
                        ) from exc
        return self._model

    def transcribe(
        self,
        audio_path: Path,
        on_segment: Callable[[TranscriptSegment], None] | None = None,
        on_stage: Callable[[str], None] | None = None,
        start_offset: float = 0.0,
    ) -> Transcription:
        """转写音频。

        三个参数全部可选，**不传时行为与阶段 2 完全一致**：

        - ``on_segment``：逐个片段回调。faster-whisper 本身就逐段产出结果，
          传入回调即可在解码过程中拿到新片段（阶段 3 用它即时落盘）；
        - ``on_stage``：阶段回调，取值为 ``"loading_model"``（模型首次加载，
          进程内只报一次）与 ``"transcribing"``（转写中）。模型加载期间没有任何
          片段可展示，调用方需要一个独立文案避免页面看起来卡死；
        - ``start_offset``：续写模式的时间偏移（阶段 3 断点续写）。传入后所有
          片段时间戳整体加上该偏移，拼回全局时间轴。
        """
        offset = max(0.0, float(start_offset))
        try:
            if on_stage is not None and self._model is None:
                on_stage("loading_model")
            model = self._get_model()
            if on_stage is not None:
                on_stage("transcribing")
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=None,
                vad_filter=True,
                beam_size=5,
            )
            # VAD 结果在此时已经算好，可以在解码前就判定“没有人声”，不浪费算力。
            self._ensure_speech_present(info)
            segments: list[TranscriptSegment] = []
            for raw_segment in raw_segments:
                text = raw_segment.text.strip()
                if not text:
                    continue
                segment = TranscriptSegment(
                    start=max(0.0, float(raw_segment.start) + offset),
                    end=max(0.0, float(raw_segment.end) + offset),
                    text=text,
                )
                segments.append(segment)
                if on_segment is not None:
                    on_segment(segment)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("TRANSCRIPTION_FAILED", "转写未完成，请重试") from exc

        text = "\n".join(segment.text for segment in segments).strip()
        if not text:
            raise AppError("EMPTY_TRANSCRIPT", DEFAULT_EMPTY_TRANSCRIPT_MESSAGE)
        duration = getattr(info, "duration", None)
        language = getattr(info, "language", None)
        return Transcription(
            text=text,
            segments=segments,
            language=language,
            duration_seconds=float(duration) if duration is not None else None,
        )

    @staticmethod
    def _ensure_speech_present(info) -> None:
        """根据 VAD 结果判定内容里到底有没有人声。

        只对“整段几乎都是静音/音乐”这种明确情况报警，阈值取得很保守，
        避免误伤“人声稀疏但确实有内容”的音视频。
        """
        duration = getattr(info, "duration", None)
        duration_after_vad = getattr(info, "duration_after_vad", None)
        if duration is None or duration_after_vad is None:
            return
        try:
            total = float(duration)
            speech = float(duration_after_vad)
        except (TypeError, ValueError):
            return
        if total <= 0:
            return
        if speech < MIN_SPEECH_SECONDS or (speech / total) < MIN_SPEECH_RATIO:
            raise AppError("SILENT_AUDIO", DEFAULT_SILENT_AUDIO_MESSAGE)
