"""B 站 API 链路（阶段 6：适配机房 IP 的线上环境）。

为什么需要它：yt-dlp 的 B 站 extractor 要抓 `www.bilibili.com` 的 HTML 页面，
而 B 站对**机房 IP** 常返回 `HTTP 412 Precondition Failed`（风控），
导致线上任务一开始就失败。但 `api.bilibili.com` 在同样的网络下是通的
（登录态自检一直有效）—— 所以链接链路改走 API：

```text
/x/web-interface/view     视频信息（标题、时长、分 P）
/x/player/v2              字幕轨（人工字幕 / AI 字幕，含字幕 URL）
/x/player/playurl         DASH 音频流地址（需 WBI 签名）
```

与本项目的其它约定保持一致：

1. 接口形状与 ``DownloadService`` 对齐（``probe`` / ``download_audio``），
   processor 不需要知道用的是哪条链路；配置项 ``PLATFORM_BACKEND`` 可切回 yt-dlp；
2. 只下载**音频流**，不下载视频；
3. 时长闸门在下载之前判断（与阶段 2 的设计一致）；
4. 凭据只从环境变量进入内存与受控 cookie 文件，不打印、不落日志；
5. 取不到字幕**不等于失败**，由编排层降级为语音转写。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from backend.app.core.errors import AppError
from backend.app.core.messages import video_too_long_message
from backend.app.services.bilibili_session import BilibiliSession
from backend.app.services.download_service import DownloadedAudio, VideoMeta


logger = logging.getLogger(__name__)

API_BASE = "https://api.bilibili.com"
VIEW_URL = f"{API_BASE}/x/web-interface/view"
PLAYER_URL = f"{API_BASE}/x/player/v2"
PLAYURL_URL = f"{API_BASE}/x/player/playurl"
NAV_URL = f"{API_BASE}/x/web-interface/nav"

VIEW_FAILED_MESSAGE = "当前链接解析失败。你可以重试，或将视频保存到本地后直接上传"
MULTI_PART_MESSAGE = "当前只支持单个视频，请粘贴某一个分集（分 P）的链接"
VIP_REQUIRED_MESSAGE = (
    "该视频为大会员专享内容，当前只能获取预览片段，无法完整转写。你可以改用本地文件上传"
)

#: WBI 签名的固定重排表（B 站前端公开实现）
MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)
WBI_KEY_TTL_SECONDS = 1800

# B 站 AI 字幕是"边生成边返回"的：同一个视频连续两次请求，返回的条目数可能差别很大。
# 如果直接采用，就会出现"只覆盖前半段，却当成完整逐字稿"的情况 —— 这正是项目禁止的
# 「假装成功」。因此这里做两件事：① 重试几次取最长；② 覆盖率不达标就不要这条字幕，
# 由编排层降级为语音转写（慢但完整）。
SUBTITLE_MIN_COVERAGE_RATIO = 0.9
SUBTITLE_FETCH_ATTEMPTS = 2
SUBTITLE_RETRY_DELAY_SECONDS = 1.5


@dataclass(frozen=True)
class AudioStream:
    url: str
    size_bytes: int | None
    bandwidth: int


class BilibiliApiService:
    """与 ``DownloadService`` 接口兼容（``probe`` / ``download_audio``）。"""

    def __init__(
        self,
        *,
        cookie: str = "",
        cookie_file_dir: Path | None = None,
        harvest_device_cookies: bool = False,
        max_media_seconds: float = float("inf"),
        max_media_minutes: int = 0,
        timeout_seconds: int = 20,
    ) -> None:
        self.session = BilibiliSession(
            cookie=cookie,
            cookie_file_dir=cookie_file_dir,
            harvest_device_cookies=harvest_device_cookies,
        )
        self.max_media_seconds = max_media_seconds
        self.max_media_minutes = max_media_minutes
        self.timeout_seconds = timeout_seconds
        self._probe_cache: dict[str, tuple[int, int]] = {}
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_keys_at = 0.0

    # ------------------------------------------------------------- 对外接口

    def probe(self, url: str) -> VideoMeta:
        video_id, requested_page = self._parse_target(url)
        view = self._get_json(VIEW_URL, {"bvid": video_id} if video_id.startswith("BV") else {"aid": video_id[2:]})
        data = view.get("data") or {}
        aid = int(data.get("aid") or 0)
        pages = data.get("pages") or []
        if not aid:
            raise AppError("VIDEO_INFO_FAILED", VIEW_FAILED_MESSAGE, status_code=400)

        duration = self._to_float(data.get("duration"))
        title = str(data.get("title") or video_id)
        cid = int(data.get("cid") or 0)

        if requested_page is not None and (requested_page < 1 or requested_page > max(1, len(pages))):
            raise AppError("INVALID_SOURCE_URL", "这个分集不存在，请确认链接", status_code=400)
        if len(pages) > 1:
            if requested_page is None:
                raise AppError("MULTI_PART_NOT_SUPPORTED", MULTI_PART_MESSAGE, status_code=400)
            page = pages[requested_page - 1]
            cid = int(page.get("cid") or cid)
            duration = self._to_float(page.get("duration")) or duration
            title = f"{title} P{requested_page}"

        self._ensure_duration_within_limit(duration)
        self._probe_cache[video_id] = (aid, cid)

        subtitles = self._collect_subtitles(aid, cid, duration=duration)
        return VideoMeta(
            video_id=video_id,
            title=title,
            duration_seconds=duration,
            webpage_url=url,
            subtitles=subtitles,
            automatic_captions={},
            subtitle_needs_login=not subtitles,
            extractor="BiliBiliApi",
        )

    def download_audio(self, url: str, destination_dir: Path, task_id: str) -> DownloadedAudio:
        video_id, requested_page = self._parse_target(url)
        cached = self._probe_cache.get(video_id)
        if cached is None:
            self.probe(url)  # 填缓存（也会顺带做时长闸门与分 P 校验）
            cached = self._probe_cache.get(video_id)
        if cached is None:
            raise AppError("VIDEO_INFO_FAILED", VIEW_FAILED_MESSAGE, status_code=400)
        aid, cid = cached

        streams = self._audio_streams(aid, cid)
        if not streams:
            raise AppError(
                "AUDIO_DOWNLOAD_FAILED",
                "未能获取该视频的音频，建议改用本地文件上传",
                status_code=400,
            )
        # 选带宽最小的音频流：转写对码率不敏感，体积小意味着更快、更省流量
        stream = min(streams, key=lambda item: item.bandwidth or 0)
        destination_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".m4a" if "mp4" in stream.url.lower() or "m4s" in stream.url.lower() else ".audio"
        path = destination_dir / f"audio{suffix}"
        size = self.session.download(stream.url, path, timeout=self.timeout_seconds * 15)
        if size <= 0:
            raise AppError(
                "AUDIO_DOWNLOAD_FAILED",
                "未能获取该视频的音频，建议改用本地文件上传",
                status_code=400,
            )
        logger.info("audio_downloaded task_id=%s bytes=%s", task_id, size)
        return DownloadedAudio(path=path, size_bytes=size)

    # ------------------------------------------------------------- 内部实现

    @staticmethod
    def _parse_target(url: str) -> tuple[str, int | None]:
        """从链接里取出视频标识与分 P 序号。"""
        import re

        match = re.search(r"/video/(?P<vid>BV[0-9A-Za-z]{10}|av\d+)", url, re.IGNORECASE)
        if match is None:
            raise AppError("INVALID_SOURCE_URL", "链接格式不正确，请粘贴完整的 B 站视频链接")
        video_id = match.group("vid")
        page_match = re.search(r"[?&]p=(\d+)", url)
        return video_id, (int(page_match.group(1)) if page_match else None)

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        try:
            payload = self.session.get_json(url, params=params, timeout=self.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - 统一成用户可理解的错误
            logger.warning("bilibili_api_failed url=%s", url, exc_info=True)
            raise AppError("VIDEO_INFO_FAILED", VIEW_FAILED_MESSAGE, status_code=400) from exc
        code = payload.get("code")
        if code not in (0, None):
            logger.info("bilibili_api_error code=%s message=%s", code, payload.get("message"))
            raise AppError("VIDEO_INFO_FAILED", VIEW_FAILED_MESSAGE, status_code=400)
        return payload

    def _collect_subtitles(self, aid: int, cid: int, *, duration: float | None) -> dict:
        """把字幕轨整理成 SubtitleService 认识的结构。

        - 键用 B 站的 ``lan``（人工字幕形如 ``zh-Hans``，AI 字幕形如 ``ai-zh``），
          因此 SubtitleService 的「人工优先于 AI」判断不需要改动；
        - 字幕正文直接取回来放进 ``data``，与 yt-dlp 下载后的结构一致；
        - **覆盖率校验**：AI 字幕可能只返回前半段，重试取最长；仍不达标就丢弃这条轨，
          让编排层降级为语音转写（宁可慢，也不要半截逐字稿）；
        - 任何一条字幕取不到就跳过它，不影响其它字幕轨。
        """
        try:
            player = self._get_json(PLAYER_URL, {"aid": aid, "cid": cid})
        except AppError:
            return {}
        tracks = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        subtitles: dict[str, list[dict]] = {}
        for track in tracks:
            language = str(track.get("lan") or "").strip()
            subtitle_url = str(track.get("subtitle_url") or "").strip()
            if not language or not subtitle_url:
                continue
            if subtitle_url.startswith("//"):
                subtitle_url = f"https:{subtitle_url}"
            payload = self._fetch_longest_subtitle(subtitle_url, duration=duration)
            if payload:
                subtitles.setdefault(language, []).append({"ext": "json", "data": payload})
        return subtitles

    def _fetch_longest_subtitle(self, subtitle_url: str, *, duration: float | None) -> str:
        """取覆盖最全的一份字幕；覆盖率不达标则返回空字符串（触发降级）。"""
        best_payload = ""
        best_coverage = 0.0
        for attempt in range(SUBTITLE_FETCH_ATTEMPTS):
            try:
                payload = self.session.get_text(subtitle_url, timeout=self.timeout_seconds)
            except Exception:  # noqa: BLE001
                logger.info("subtitle_fetch_failed attempt=%s", attempt + 1)
                payload = ""
            coverage = self._subtitle_coverage(payload)
            if coverage >= best_coverage:
                best_payload, best_coverage = payload, coverage
            if duration is None or coverage >= duration * SUBTITLE_MIN_COVERAGE_RATIO:
                return best_payload
            logger.info(
                "subtitle_incomplete attempt=%s coverage=%.1fs duration=%.1fs",
                attempt + 1,
                coverage,
                duration,
            )
            if attempt < SUBTITLE_FETCH_ATTEMPTS - 1:
                time.sleep(SUBTITLE_RETRY_DELAY_SECONDS)
        return ""

    @staticmethod
    def _subtitle_coverage(payload: str) -> float:
        """字幕覆盖到的最大时间点（秒）。解析失败按 0 处理。"""
        if not payload or not payload.strip():
            return 0.0
        import json

        try:
            body = (json.loads(payload) or {}).get("body") or []
        except (TypeError, ValueError):
            return 0.0
        latest = 0.0
        for item in body:
            if not isinstance(item, dict):
                continue
            try:
                latest = max(latest, float(item.get("to") or 0.0))
            except (TypeError, ValueError):
                continue
        return latest

    def _audio_streams(self, aid: int, cid: int) -> list[AudioStream]:
        params = self._sign({"avid": aid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1})
        payload = self._get_json(PLAYURL_URL, params)
        data = payload.get("data") or {}
        streams: list[AudioStream] = []

        dash = data.get("dash") or {}
        for item in dash.get("audio") or []:
            url = str(item.get("baseUrl") or item.get("base_url") or "").strip()
            if not url:
                continue
            streams.append(
                AudioStream(
                    url=url,
                    size_bytes=self._to_int(item.get("size")),
                    bandwidth=self._to_int(item.get("bandwidth")) or 0,
                )
            )
        if not streams:
            # 少数视频没有 DASH 音频，退回 durl（合并流），后续仍会只取音频
            for item in data.get("durl") or []:
                url = str(item.get("url") or "").strip()
                if url:
                    streams.append(
                        AudioStream(
                            url=url,
                            size_bytes=self._to_int(item.get("size")),
                            bandwidth=10**9,
                        )
                    )
        return streams

    def _sign(self, params: dict) -> dict:
        """WBI 签名：playurl 需要它才会返回音频地址。"""
        img_key, sub_key = self._wbi_keys_pair()
        mixin_key = self._mixin_key(img_key, sub_key)
        signed = {**params, "wts": int(time.time())}
        filtered = {
            key: "".join(char for char in str(value) if char not in "!'()*")
            for key, value in signed.items()
        }
        query = urlencode(sorted(filtered.items()))
        signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode()).hexdigest()  # noqa: S324
        return signed

    def _wbi_keys_pair(self) -> tuple[str, str]:
        now = time.monotonic()
        if self._wbi_keys and now - self._wbi_keys_at < WBI_KEY_TTL_SECONDS:
            return self._wbi_keys
        try:
            nav = self._get_json(NAV_URL)
            wbi = (nav.get("data") or {}).get("wbi_img") or {}
            img_key = Path(str(wbi.get("img_url") or "")).stem
            sub_key = Path(str(wbi.get("sub_url") or "")).stem
        except AppError:
            img_key = sub_key = ""
        if img_key and sub_key:
            self._wbi_keys = (img_key, sub_key)
            self._wbi_keys_at = now
        return self._wbi_keys or ("", "")

    @staticmethod
    def _mixin_key(img_key: str, sub_key: str) -> str:
        raw = f"{img_key}{sub_key}"
        if len(raw) < 64:
            return raw
        return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]

    def _ensure_duration_within_limit(self, duration_seconds: float | None) -> None:
        if duration_seconds is None or duration_seconds <= self.max_media_seconds:
            return
        raise AppError(
            "VIDEO_TOO_LONG",
            video_too_long_message(duration_seconds, self.max_media_minutes),
        )

    @staticmethod
    def _to_float(value: object) -> float | None:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
