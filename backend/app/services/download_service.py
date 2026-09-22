from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from backend.app.core.errors import AppError


logger = logging.getLogger(__name__)

PLATFORM_EXTRACTORS = {"BiliBili", "BiliBiliBangumi", "BiliBiliSearch"}

VIDEO_TOO_LONG_TEMPLATE = (
    "该视频时长约 {hours}，超过当前上限 {limit_minutes} 分钟。建议分段处理，或改用本地文件上传"
)
MULTI_PART_MESSAGE = "当前只支持单个视频，请粘贴某一个分集（分 P）的链接"
# 大会员专享视频只能拿到预览片段。yt-dlp 对此只发警告不报错，
# 若不拦截，会用几分钟的预览片段冒充完整视频，属于“假装成功”。
VIP_REQUIRED_MESSAGE = (
    "该视频为大会员专享内容，当前只能获取预览片段，无法完整转写。你可以改用本地文件上传"
)
PREVIEW_WARNING_TOKENS = ("premium member", "only preview format")


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    duration_seconds: float | None
    webpage_url: str
    subtitles: dict
    automatic_captions: dict
    subtitle_needs_login: bool
    extractor: str


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    size_bytes: int


class _YdlLogger:
    """把 yt-dlp 的输出收进日志，并单独记下“字幕需要登录”这一关键提示。

    只转发 yt-dlp 自身产生的文本；不打印任何请求参数，避免 Cookie 进入日志。
    """

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.subtitle_login_required = False
        self.preview_only = False

    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        self._record(message, logging.INFO)

    def error(self, message: str) -> None:
        self._record(message, logging.WARNING)

    def _record(self, message: str, level: int) -> None:
        text = str(message)
        self.messages.append(text)
        lowered = text.lower()
        if "logged in" in lowered or "login" in lowered:
            self.subtitle_login_required = True
        if any(token in lowered for token in PREVIEW_WARNING_TOKENS):
            self.preview_only = True
        logger.log(level, "yt_dlp: %s", text)


def _format_hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours >= 1:
        return f"{hours:.1f} 小时"
    return f"{int(seconds // 60)} 分钟"


class DownloadService:
    """B 站视频信息解析与音频获取。

    设计要点（均经真实链接实测确认）：

    1. 探测阶段**不设置** ``noplaylist``，这样多 P 视频会返回 playlist 结构，
       我们才能识别并给出明确提示；若设置该项，yt-dlp 会静默只取第一个分 P。
    2. 必须显式开启 ``writesubtitles``，否则 yt-dlp 根本不会去取字幕；
       用这一项而不是 ``listsubtitles``，因为后者会向 stdout 打印字幕表格。
    3. 只下载音频轨（``bestaudio``），不下载视频轨。
    4. 时长闸门在**下载之前**用元信息判断，避免下载完才失败。
    5. 登录凭据必须通过 **Netscape cookie 文件**传入，不能用 ``http_headers``：
       yt-dlp 会把请求头里的 Cookie 限域到下载 URL 的主机名
       （即 ``.www.bilibili.com``），而字幕接口在 ``api.bilibili.com``，
       导致 Cookie 根本发不到字幕接口，表现为“已登录但取不到字幕”。
       写 cookie 文件时用 ``.bilibili.com`` 作为 domain，可覆盖两个子域。
    """

    COOKIE_FILE_NAME = "bilibili-cookies.txt"
    COOKIE_DOMAIN = ".bilibili.com"

    def __init__(
        self,
        *,
        cookie: str = "",
        cookie_file_dir: Path | None = None,
        proxy: str = "",
        max_media_seconds: float = 180 * 60,
        max_media_minutes: int = 180,
        max_download_bytes: int = 1024 * 1024 * 1024,
        socket_timeout_seconds: int = 25,
        rate_limit_kbps: int | None = None,
    ) -> None:
        self.cookie = cookie
        self.cookie_file_dir = cookie_file_dir
        self.proxy = proxy
        self.max_media_seconds = max_media_seconds
        self.max_media_minutes = max_media_minutes
        self.max_download_bytes = max_download_bytes
        self.socket_timeout_seconds = socket_timeout_seconds
        self.rate_limit_kbps = rate_limit_kbps

    # ------------------------------------------------------------------ 探测

    def probe(self, url: str) -> VideoMeta:
        info, ydl_logger = self._extract(url, download=False, fetch_subtitles=True, noplaylist=False)
        _raise_if_preview_only(ydl_logger)

        if info.get("_type") == "playlist" or info.get("entries") is not None:
            raise AppError("MULTI_PART_NOT_SUPPORTED", MULTI_PART_MESSAGE)

        extractor = str(info.get("extractor") or "")
        if not any(extractor.startswith(name) for name in PLATFORM_EXTRACTORS):
            raise AppError("UNSUPPORTED_PLATFORM", "当前仅支持 B 站链接。你也可以把视频保存到本地后直接上传")

        duration = info.get("duration")
        duration_seconds = float(duration) if isinstance(duration, (int, float)) else None
        if duration_seconds is not None and duration_seconds > self.max_media_seconds:
            raise AppError(
                "VIDEO_TOO_LONG",
                VIDEO_TOO_LONG_TEMPLATE.format(
                    hours=_format_hours(duration_seconds),
                    limit_minutes=self.max_media_minutes,
                ),
            )

        subtitles = info.get("subtitles") or {}
        automatic_captions = info.get("automatic_captions") or {}
        if ydl_logger.subtitle_login_required and not _has_real_subtitles(subtitles):
            logger.info("subtitle_unavailable_without_login video_id=%s", info.get("id"))

        return VideoMeta(
            video_id=str(info.get("id") or ""),
            title=str(info.get("title") or "").strip(),
            duration_seconds=duration_seconds,
            webpage_url=str(info.get("webpage_url") or url),
            subtitles=subtitles,
            automatic_captions=automatic_captions,
            subtitle_needs_login=ydl_logger.subtitle_login_required,
            extractor=extractor,
        )

    # ------------------------------------------------------------------ 下载

    def download_audio(self, url: str, destination_dir: Path, task_id: str) -> DownloadedAudio:
        destination_dir.mkdir(parents=True, exist_ok=True)
        options = self._base_options()
        options.update(
            {
                "skip_download": False,
                "noplaylist": True,
                "format": "bestaudio/best",
                "outtmpl": str(destination_dir / "audio.%(ext)s"),
                "max_filesize": self.max_download_bytes,
                "overwrites": True,
            }
        )
        if self.rate_limit_kbps:
            options["ratelimit"] = self.rate_limit_kbps * 1024

        try:
            info, ydl_logger = self._extract(url, download=True, options=options)
            _raise_if_preview_only(ydl_logger)
            path = self._resolve_downloaded_path(info, destination_dir)
        except AppError as exc:
            self._cleanup(destination_dir)
            if exc.code == "VIDEO_INFO_FAILED":
                raise AppError("AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传") from exc
            raise
        except Exception as exc:
            self._cleanup(destination_dir)
            logger.warning("audio_download_failed task_id=%s type=%s", task_id, type(exc).__name__)
            raise AppError(
                "AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传"
            ) from exc

        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            self._cleanup(destination_dir)
            raise AppError("AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传")
        if size_bytes > self.max_download_bytes:
            self._cleanup(destination_dir)
            raise AppError("DOWNLOAD_TOO_LARGE", "音频文件过大，已停止下载。建议分段处理")
        return DownloadedAudio(path=path, size_bytes=size_bytes)

    # ------------------------------------------------------------------ 内部

    def _base_options(self) -> dict:
        options: dict = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "nocheckcertificate": False,
            "socket_timeout": self.socket_timeout_seconds,
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "ignoreerrors": False,
        }
        if self.proxy:
            options["proxy"] = self.proxy
        cookie_file = self._ensure_cookie_file()
        if cookie_file is not None:
            options["cookiefile"] = str(cookie_file)
        return options

    def _ensure_cookie_file(self) -> Path | None:
        """把环境变量里的 Cookie 转成 yt-dlp 能正确限域的 Netscape cookie 文件。

        文件放在受控目录并设为仅当前用户可读，不进版本控制。
        """
        if not self.cookie:
            return None
        if self.cookie_file_dir is None:
            raise AppError(
                "COOKIE_FILE_DIR_MISSING",
                "登录凭据配置不完整，请检查服务配置",
                status_code=503,
            )
        directory = Path(self.cookie_file_dir)
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path = directory / self.COOKIE_FILE_NAME
        content = self._cookie_file_content()
        try:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != content:
                path.write_text(content, encoding="utf-8")
                os.chmod(path, 0o600)
        except OSError as exc:
            raise AppError(
                "COOKIE_FILE_WRITE_FAILED",
                "登录凭据写入失败，请检查数据目录权限",
                status_code=503,
            ) from exc
        return path

    def _cookie_file_content(self) -> str:
        lines = [
            "# Netscape HTTP Cookie File",
            "# 由本项目从 BILIBILI_COOKIE 生成，请勿提交到版本控制或外传",
        ]
        for name, value in self._cookie_pairs():
            lines.append(
                "\t".join([self.COOKIE_DOMAIN, "TRUE", "/", "TRUE", "0", name, value])
            )
        return "\n".join(lines) + "\n"

    def _cookie_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for part in self.cookie.split(";"):
            name, separator, value = part.strip().partition("=")
            name, value = name.strip(), value.strip()
            if separator and name and value:
                pairs.append((name, value))
        return pairs

    def _extract(
        self,
        url: str,
        *,
        download: bool,
        options: dict | None = None,
        fetch_subtitles: bool = False,
        noplaylist: bool = False,
    ) -> tuple[dict, _YdlLogger]:
        import yt_dlp

        ydl_logger = _YdlLogger()
        if options is None:
            options = self._base_options()
            options["skip_download"] = not download
        options["logger"] = ydl_logger
        if fetch_subtitles:
            # 触发 extractor 去取字幕；与 listsubtitles 不同，不会打印字幕表格。
            options["writesubtitles"] = True
        if download:
            options["noplaylist"] = True
        elif not noplaylist:
            options.pop("noplaylist", None)

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=download)
        except Exception as exc:
            raise self._map_error(exc, download=download) from exc
        if not isinstance(info, dict):
            raise AppError(
                "VIDEO_INFO_FAILED",
                "当前链接解析失败。你可以重试，或将视频保存到本地后直接上传",
            )
        return info, ydl_logger

    def _resolve_downloaded_path(self, info: dict, destination_dir: Path) -> Path:
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
            (p for p in destination_dir.glob("audio.*") if p.is_file()),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if not produced:
            raise AppError("AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传")
        return produced[0]

    @staticmethod
    def _cleanup(directory: Path) -> None:
        if not directory.is_dir():
            return
        for item in directory.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)

    @staticmethod
    def _map_error(exc: Exception, *, download: bool) -> AppError:
        text = str(exc)
        lowered = text.lower()
        if "maximum file size" in lowered or "max-filesize" in lowered or "max_filesize" in lowered:
            return AppError("DOWNLOAD_TOO_LARGE", "音频文件过大，已停止下载。建议分段处理")
        if "logged in" in lowered or "login" in lowered or "sessdata" in lowered or "大会员" in text:
            return AppError(
                "LOGIN_REQUIRED",
                "该视频需要登录后才能访问，当前无法处理。你可以改用本地文件上传",
            )
        if any(
            token in lowered
            for token in ("not available", "unavailable", "404", "not found", "no video formats")
        ) or any(token in text for token in ("不存在", "已删除", "地区", "失效")):
            return AppError(
                "VIDEO_UNAVAILABLE",
                "无法获取该视频信息，可能是视频已删除或受限。你可以改用本地文件上传",
            )
        if download:
            return AppError("AUDIO_DOWNLOAD_FAILED", "未能获取该视频的音频，建议改用本地文件上传")
        return AppError(
            "VIDEO_INFO_FAILED", "当前链接解析失败。你可以重试，或将视频保存到本地后直接上传"
        )


def _raise_if_preview_only(ydl_logger: _YdlLogger) -> None:
    if ydl_logger.preview_only:
        raise AppError("VIP_REQUIRED", VIP_REQUIRED_MESSAGE)


def _has_real_subtitles(subtitles: dict) -> bool:
    """排除弹幕：B 站把 danmaku（弹幕 XML）也放在 subtitles 里，它不是字幕。"""
    return any(lang for lang in subtitles if lang != "danmaku")
