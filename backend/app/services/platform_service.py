from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from backend.app.core.errors import AppError
from backend.app.schemas.task import Platform


MAX_URL_LENGTH = 2048

# 平台域名白名单。只接受这些域名（或其子域名），其余一律拒绝。
BILIBILI_DOMAINS = {"bilibili.com"}
SHORT_LINK_DOMAINS = {"b23.tv"}

# 普通投稿视频：/video/BVxxxxxxxxxx 或 /video/av123456
VIDEO_PATH_PATTERN = re.compile(r"^/video/(?P<video_id>BV[0-9A-Za-z]{10}|av\d+)/?$", re.IGNORECASE)

INVALID_URL_MESSAGE = "链接格式不正确，请粘贴完整的 B 站视频链接"
UNSUPPORTED_PLATFORM_MESSAGE = "当前仅支持 B 站链接。你也可以把视频保存到本地后直接上传"


@dataclass(frozen=True)
class ResolvedSource:
    platform: Platform
    url: str
    original_url: str
    video_id: str


def _is_under(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


class PlatformService:
    """平台识别、URL 白名单校验与短链解析。

    安全底线：不接受任意 URL 直接进入下载器。域名白名单在服务端强制执行，
    短链跳转后会再做一次白名单校验，避免通过跳转绕过。
    """

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        proxy: str = "",
        short_link_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy
        self._short_link_resolver = short_link_resolver or self._request_short_link

    def resolve(self, raw_url: str) -> ResolvedSource:
        normalized = self._validate_syntax(raw_url)
        host = self._host_of(normalized)
        if _is_under(host, SHORT_LINK_DOMAINS):
            normalized = self._resolve_short_link(normalized)
            host = self._host_of(normalized)
            if not _is_under(host, BILIBILI_DOMAINS):
                raise AppError("UNSUPPORTED_PLATFORM", UNSUPPORTED_PLATFORM_MESSAGE)
        match = VIDEO_PATH_PATTERN.match(urlsplit(normalized).path)
        if not match:
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        return ResolvedSource(
            platform=Platform.BILIBILI,
            url=normalized,
            original_url=raw_url.strip(),
            video_id=match.group("video_id"),
        )

    # ------------------------------------------------------------------ 内部

    def _validate_syntax(self, raw_url: str) -> str:
        if not isinstance(raw_url, str):
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        candidate = raw_url.strip()
        if not candidate or len(candidate) > MAX_URL_LENGTH:
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        host = parts.hostname.lower().rstrip(".")
        if not (_is_under(host, BILIBILI_DOMAINS) or _is_under(host, SHORT_LINK_DOMAINS)):
            raise AppError("UNSUPPORTED_PLATFORM", UNSUPPORTED_PLATFORM_MESSAGE)
        return candidate

    @staticmethod
    def _host_of(url: str) -> str:
        return (urlsplit(url).hostname or "").lower().rstrip(".")

    def _resolve_short_link(self, url: str) -> str:
        try:
            final_url = self._short_link_resolver(url)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "VIDEO_INFO_FAILED",
                "当前链接解析失败。你可以重试，或将视频保存到本地后直接上传",
            ) from exc
        if not isinstance(final_url, str) or not final_url.strip():
            raise AppError(
                "VIDEO_INFO_FAILED",
                "当前链接解析失败。你可以重试，或将视频保存到本地后直接上传",
            )
        return final_url.strip()

    def _request_short_link(self, url: str) -> str:
        import requests

        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        session = requests.Session()
        session.max_redirects = 5
        response = session.get(
            url,
            timeout=self.timeout_seconds,
            allow_redirects=True,
            proxies=proxies,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com",
            },
        )
        return response.url
