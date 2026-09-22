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
            raise AppError("MEDIA_HAS_NO_AUDIO", "该文件没有可转写的音轨")
        if expected_type == MediaType.VIDEO and not has_video:
            raise AppError("MEDIA_TYPE_MISMATCH", "文件内容与视频格式不匹配")
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
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppError("AUDIO_EXTRACTION_FAILED", "未能从视频中提取音频") from exc
        if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            raise AppError("AUDIO_EXTRACTION_FAILED", "未能从视频中提取音频")
        return destination
