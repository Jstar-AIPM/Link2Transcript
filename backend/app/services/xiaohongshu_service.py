"""小红书链接链路（阶段 5）：视频信息解析与媒体下载。

设计要点（均来自真实链接实测，见《第五阶段前期平台可行性验证记录》）：

1. **短链先由 PlatformService 解析**：`xhslink.com` / `xhslink.cn` 用移动端 UA
   跟一次跳转，得到 `www.xiaohongshu.com/discovery/item/<id>?xsec_token=...`；
   本服务拿到的是**已含 xsec_token 的真实地址**，因此必须原样传给 yt-dlp（不能丢查询串）；
2. **用桌面 UA** 请求：实测桌面 UA 能拿到 formats，移动端 UA 反而报 “No video formats found”；
3. **没有独立字幕轨，也没有纯音频流** → 下载的是视频容器，由编排层再提取音轨后转写；
4. 接口形状与 ``BilibiliApiService`` / ``DownloadService`` 对齐（``probe`` / ``download_audio``），
   编排层不需要知道用的是哪个平台。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.core.messages import video_too_long_message
from backend.app.services.download_service import DownloadedAudio, VideoMeta


logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REFERER = "https://www.xiaohongshu.com/"

PROBE_FAILED_MESSAGE = "当前链接解析失败。你可以重试，或换一条链接"
DOWNLOAD_FAILED_MESSAGE = "未能获取该视频，你可以重试，或换一条链接"


class _YdlLogger:
    """把 yt-dlp 的告警收进日志（不打印请求参数）。"""

    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        logger.info("yt_dlp_xhs: %s", str(message))

    def error(self, message: str) -> None:
        logger.warning("yt_dlp_xhs: %s", str(message))


class XiaohongshuService:
    """与 ``BilibiliApiService`` 接口兼容（``probe`` / ``download_audio``）。"""

    def __init__(
        self,
        *,
        max_media_seconds: float = float("inf"),
        max_media_minutes: int = 0,
        max_download_bytes: int = 1024 * 1024 * 1024,
        socket_timeout_seconds: int = 25,
    ) -> None:
        self.max_media_seconds = max_media_seconds
        self.max_media_minutes = max_media_minutes
        self.max_download_bytes = max_download_bytes
        self.socket_timeout_seconds = socket_timeout_seconds

    # ------------------------------------------------------------- 对外接口

    def probe(self, url: str) -> VideoMeta:
        info, _ = self._extract(url, download=False)
        duration = info.get("duration")
        duration_seconds = float(duration) if isinstance(duration, (int, float)) else None
        if duration_seconds is not None and duration_seconds > self.max_media_seconds:
            raise AppError(
                "VIDEO_TOO_LONG",
                video_too_long_message(duration_seconds, self.max_media_minutes),
            )
        return VideoMeta(
            video_id=str(info.get("id") or ""),
            title=str(info.get("title") or "").strip(),
            duration_seconds=duration_seconds,
            webpage_url=str(info.get("webpage_url") or url),
            # 实测小红书没有独立字幕轨：留空即可，编排层会走语音转写
            subtitles=info.get("subtitles") or {},
            automatic_captions=info.get("automatic_captions") or {},
            subtitle_needs_login=False,
            extractor=str(info.get("extractor") or ""),
        )

    def download_audio(self, url: str, destination_dir: Path, task_id: str) -> DownloadedAudio:
        destination_dir.mkdir(parents=True, exist_ok=True)
        options = self._base_options()
        options.update(
            {
                "skip_download": False,
                "noplaylist": True,
                # 小红书没有纯音频流，bestaudio 不存在时会回落到 best（视频容器），
                # 由编排层提取音轨后再转写。
                "format": "bestaudio/best",
                "outtmpl": str(destination_dir / "media.%(ext)s"),
                "max_filesize": self.max_download_bytes,
                "overwrites": True,
            }
        )
        try:
            info, _ = self._extract(url, download=True, options=options)
            path = self._resolve_downloaded_path(info, destination_dir)
        except AppError:
            self._cleanup(destination_dir)
            raise
        except Exception as exc:  # noqa: BLE001
            self._cleanup(destination_dir)
            logger.warning("xhs_download_failed task_id=%s type=%s", task_id, type(exc).__name__)
            raise AppError("AUDIO_DOWNLOAD_FAILED", DOWNLOAD_FAILED_MESSAGE) from exc

        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            self._cleanup(destination_dir)
            raise AppError("AUDIO_DOWNLOAD_FAILED", DOWNLOAD_FAILED_MESSAGE)
        if size_bytes > self.max_download_bytes:
            self._cleanup(destination_dir)
            raise AppError("DOWNLOAD_TOO_LARGE", "视频文件过大，已停止下载")
        return DownloadedAudio(path=path, size_bytes=size_bytes)

    # ------------------------------------------------------------------ 内部

    def _base_options(self) -> dict:
        return {
            "http_headers": {"User-Agent": USER_AGENT, "Referer": REFERER},
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "nocheckcertificate": False,
            "socket_timeout": self.socket_timeout_seconds,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "ignoreerrors": False,
            # 探测字幕轨；小红书实际不会返回字幕，行为与“没有字幕”一致
            "writesubtitles": True,
            "logger": _YdlLogger(),
        }

    def _extract(self, url: str, *, download: bool, options: dict | None = None) -> tuple[dict, _YdlLogger]:
        import yt_dlp

        opts = dict(options or self._base_options())
        opts.setdefault("skip_download", not download)
        opts["logger"] = _YdlLogger()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=download)
        except Exception as exc:
            raise self._map_error(exc) from exc
        if not isinstance(info, dict):
            raise AppError("VIDEO_INFO_FAILED", PROBE_FAILED_MESSAGE)
        # 合集/图文等结构只取第一条视频
        if (info.get("_type") == "playlist" or info.get("entries") is not None):
            entries = info.get("entries") or []
            info = next((item for item in entries if isinstance(item, dict)), info)
        return info, opts["logger"]

    @staticmethod
    def _resolve_downloaded_path(info: dict, destination_dir: Path) -> Path:
        requested = info.get("requested_downloads") or []
        candidates: list[str] = []
        for entry in requested:
            if isinstance(entry, dict) and entry.get("filepath"):
                candidates.append(str(entry["filepath"]))
        for key in ("filepath", "_filename"):
            if info.get(key):
                candidates.append(str(info[key]))
        for candidate in candidates:
            path = Path(candidate)
            if path.is_file():
                return path
        produced = sorted(
            (p for p in destination_dir.glob("media.*") if p.is_file()),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if not produced:
            raise AppError("AUDIO_DOWNLOAD_FAILED", DOWNLOAD_FAILED_MESSAGE)
        return produced[0]

    @staticmethod
    def _cleanup(directory: Path) -> None:
        if not directory.is_dir():
            return
        for item in directory.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)

    @staticmethod
    def _map_error(exc: Exception) -> AppError:
        text = str(exc)
        lowered = text.lower()
        if "no video formats" in lowered:
            return AppError("VIDEO_INFO_FAILED", PROBE_FAILED_MESSAGE)
        if any(token in lowered for token in ("not found", "404", "unavailable", "removed")):
            return AppError("VIDEO_UNAVAILABLE", "无法获取该视频信息，可能是内容已删除或受限")
        return AppError("VIDEO_INFO_FAILED", PROBE_FAILED_MESSAGE)
