from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.app.core.errors import AppError
from backend.app.schemas.task import TranscriptSegment


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
    ) -> Transcription:
        """转写音频。

        ``on_segment`` 是为止后阶段预留的逐个片段回调口子：faster-whisper 本身就
        逐个片段产出结果，传入回调即可在解码过程中拿到新片段。阶段 2 不传该参数，
        行为与阶段 1 完全一致。
        """
        try:
            model = self._get_model()
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=None,
                vad_filter=True,
                beam_size=5,
            )
            segments: list[TranscriptSegment] = []
            for raw_segment in raw_segments:
                text = raw_segment.text.strip()
                if not text:
                    continue
                segment = TranscriptSegment(
                    start=max(0.0, float(raw_segment.start)),
                    end=max(0.0, float(raw_segment.end)),
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
            raise AppError("EMPTY_TRANSCRIPT", "没有识别到可用的语音内容")
        duration = getattr(info, "duration", None)
        language = getattr(info, "language", None)
        return Transcription(
            text=text,
            segments=segments,
            language=language,
            duration_seconds=float(duration) if duration is not None else None,
        )
