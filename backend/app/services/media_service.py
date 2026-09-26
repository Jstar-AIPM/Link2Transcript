from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.schemas.task import MediaType


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float | None
    has_audio: bool
    has_video: bool


def media_type_for_filename(filename: str) -> MediaType:
    suffix = Path(filename).suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    if suffix in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    raise AppError("UNSUPPORTED_FILE_TYPE", "当前不支持该文件格式")


class MediaService:
    def ensure_tools(self) -> None:
        try:
            executable = self._ffmpeg_executable()
        except Exception as exc:
            raise AppError(
                "MEDIA_TOOL_UNAVAILABLE",
                "媒体处理组件不可用，请重新安装项目依赖",
                status_code=503,
            ) from exc
        if not executable.is_file():
            raise AppError(
                "MEDIA_TOOL_UNAVAILABLE",
                "媒体处理组件不可用，请重新安装项目依赖",
                status_code=503,
            )

    def _ffmpeg_executable(self) -> Path:
        from imageio_ffmpeg import get_ffmpeg_exe

        return Path(get_ffmpeg_exe())

    def inspect(self, source: Path, expected_type: MediaType) -> MediaInfo:
        self.ensure_tools()
        try:
            import av

            with av.open(str(source)) as container:
                has_audio = bool(container.streams.audio)
                has_video = bool(container.streams.video)
                duration = (
                    float(container.duration / av.time_base)
                    if container.duration is not None
                    else None
                )
        except Exception as exc:
            raise AppError("INVALID_MEDIA_FILE", "文件已损坏或不是有效的音视频文件") from exc

        if not has_audio:
            raise AppError("MEDIA_HAS_NO_AUDIO", "该文件不包含音轨，无法生成逐字稿。请确认文件是否正确。")
        if expected_type == MediaType.VIDEO and not has_video:
            raise AppError("MEDIA_TYPE_MISMATCH", "文件内容与其扩展名不一致，无法处理。请确认文件是否正确。")
        return MediaInfo(duration_seconds=duration, has_audio=has_audio, has_video=has_video)

    def extract_audio(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self._ffmpeg_executable()),
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
        self._run_ffmpeg(command, destination, "未能从视频中提取音频", "AUDIO_EXTRACTION_FAILED")
        return destination

    def slice_audio(
        self,
        source: Path,
        destination: Path,
        start_seconds: float,
        duration_seconds: float | None = None,
    ) -> Path:
        """截取一段音频。

        - 只给 ``start_seconds`` 时截到结尾，用于断点续写（阶段 3B）；
        - 同时给 ``duration_seconds`` 时只截取该长度，用于长内容分窗转写（阶段 3.5）。

        ``-ss`` 放在 ``-i`` 之前：走快速定位，长音频不需要从头解码。
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self._ffmpeg_executable()),
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, start_seconds):.3f}",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
        ]
        if duration_seconds is not None and duration_seconds > 0:
            # -t 是输出选项，放在输入之后、输出文件之前
            command.extend(["-t", f"{duration_seconds:.3f}"])
        command.extend(["-c:a", "pcm_s16le", str(destination)])
        self._run_ffmpeg(command, destination, "未能续写已中断的任务", "AUDIO_SLICE_FAILED")
        return destination

    def _run_ffmpeg(
        self, command: list[str], destination: Path, message: str, code: str
    ) -> None:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppError(code, message) from exc
        if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            raise AppError(code, message)
