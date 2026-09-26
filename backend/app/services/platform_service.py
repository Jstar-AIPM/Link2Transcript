"""平台识别、URL 白名单校验与短链解析。

安全底线：**不接受任意 URL 直接进入下载器**。域名白名单在服务端强制执行，
短链跳转后会再做一次白名单校验，避免通过跳转绕过。

阶段 5 起改为**适配器注册表**：每个平台实现一个 ``PlatformAdapter``（域名集合、
短链域名、URL 解析规则），``PlatformService`` 只负责「校验语法 → 找到适配器 →
（必要时）解析短链 → 交给适配器解析」。新增平台只需注册一个适配器，
不需要改动主链路与调用方。

当前启用：**B 站**（``BilibiliAdapter``）与**小红书**（``XiaohongshuAdapter``）。
抖音尚未启用：其接口依赖页面 JS 生成的签名 Cookie，纯 HTTP 取不到数据
（见《第五阶段前期平台可行性验证记录》），因此这里给出**专门的中文提示**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit

from backend.app.core.errors import AppError
from backend.app.schemas.task import Platform


MAX_URL_LENGTH = 2048

INVALID_URL_MESSAGE = "链接格式不正确，请粘贴完整的视频链接"

#: 用户在 App 里点“复制链接”时，拿到的是**一整段分享文案**（标题 + 链接 + 口令）。
#: 工具应当接受整段粘贴，由我们把它里真实的链接提取出来 —— 不让用户去手工删多余文字。
URL_IN_TEXT_PATTERN = re.compile(r"https?://[^\s\u3000<>\"']+", re.IGNORECASE)
#: 链接贴在句末时常被标点粘上，提取后要去掉
TRAILING_PUNCTUATION = "。，、；：！？）】》」』…,.!?;:)]}\"'"
NO_URL_IN_TEXT_MESSAGE = "没有找到链接，请把包含链接的分享内容整段粘贴进来"

#: 部分平台需要移动端 UA 才会返回真实的分享目标地址（小红书短链就是如此）
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 已知但**尚未启用**的平台：给专门的提示，而不是笼统的“仅支持 B 站”。
PENDING_PLATFORM_DOMAINS: dict[str, str] = {
    "douyin.com": "抖音",
    "iesdouyin.com": "抖音",
}


def first_url_in_text(raw: str) -> str | None:
    """从一段（可能带标题/口令的）分享文案里提取第一个 http(s) 链接。

    找不到则返回 ``None``。只取第一个链接：分享文案里通常就一个。
    """
    if not isinstance(raw, str):
        return None
    match = URL_IN_TEXT_PATTERN.search(raw)
    if match is None:
        return None
    return match.group(0).rstrip(TRAILING_PUNCTUATION)


def _is_under(host: str, domains: frozenset[str] | set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


@dataclass(frozen=True)
class ResolvedSource:
    platform: Platform
    url: str
    original_url: str
    video_id: str


class PlatformAdapter:
    """一个平台需要提供的全部信息与解析逻辑。

    子类只需给出 ``platform`` / ``name`` / ``domains`` / ``short_link_domains``，
    并实现 ``parse``。绝大多数平台是「固定路径 + 正则取 id」，可直接复用
    ``RegexPathAdapter``。
    """

    #: 注意：这里只声明注解、不给默认值。子类是 dataclass，若基类同名属性带了
    #: 默认值，会被 dataclass 当成“有默认的字段”，导致字段顺序报错。
    platform: Platform
    name: str  #: 用户可见的平台名（出现在「当前仅支持…」这类提示里）
    domains: frozenset[str]
    short_link_domains: frozenset[str]

    def matches(self, host: str) -> bool:
        return _is_under(host, self.domains)

    def is_short_link(self, host: str) -> bool:
        return _is_under(host, self.short_link_domains)

    def parse(self, url: str) -> tuple[str, str]:
        """把（已确认属于本平台的）URL 解析成 ``(video_id, 规范化 url)``。

        无法识别时抛 ``INVALID_SOURCE_URL``。
        """
        raise NotImplementedError


@dataclass(frozen=True)
class RegexPathAdapter(PlatformAdapter):
    """最通用的适配器：域名 + 路径正则取视频 id。"""

    platform: Platform
    name: str
    domains: frozenset[str]
    path_pattern: re.Pattern[str]
    invalid_message: str = INVALID_URL_MESSAGE
    short_link_domains: frozenset[str] = frozenset()
    #: 平台主页，用作短链解析时的 Referer
    homepage: str = ""
    #: 解析短链时使用的 UA；留空表示用桌面 UA（部分平台需要移动端 UA）
    short_link_user_agent: str = ""

    def parse(self, url: str) -> tuple[str, str]:
        match = self.path_pattern.match(urlsplit(url).path)
        if not match:
            raise AppError("INVALID_SOURCE_URL", self.invalid_message)
        # 注意：这里返回**完整 URL（含查询串）**。小红书的 xsec_token 就在查询串里，
        # 丢掉它会导致后续解析失败。
        return match.group("video_id"), url


BilibiliAdapter = RegexPathAdapter(
    platform=Platform.BILIBILI,
    name="B站",
    domains=frozenset({"bilibili.com"}),
    short_link_domains=frozenset({"b23.tv"}),
    path_pattern=re.compile(r"^/video/(?P<video_id>BV[0-9A-Za-z]{10}|av\d+)/?$", re.IGNORECASE),
    homepage="https://www.bilibili.com",
)

XiaohongshuAdapter = RegexPathAdapter(
    platform=Platform.XIAOHONGSHU,
    name="小红书",
    domains=frozenset({"xiaohongshu.com"}),
    short_link_domains=frozenset({"xhslink.com", "xhslink.cn"}),
    path_pattern=re.compile(
        r"^/(?:discovery/item|explore)/(?P<video_id>[0-9a-fA-F]{24})/?$"
    ),
    homepage="https://www.xiaohongshu.com",
    # 实测：桌面 UA 访问 xhslink 会跳到登录页，移动 UA 才会给出真实笔记地址
    short_link_user_agent=MOBILE_USER_AGENT,
)


def default_adapters() -> list[PlatformAdapter]:
    """当前启用的平台适配器。新增平台时在这里追加。"""
    return [BilibiliAdapter, XiaohongshuAdapter]


class PlatformService:
    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        proxy: str = "",
        short_link_resolver: Callable[[str], str] | None = None,
        adapters: Sequence[PlatformAdapter] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy
        self.adapters: list[PlatformAdapter] = (
            list(adapters) if adapters is not None else default_adapters()
        )
        # 测试会注入一个假的解析器来绕过真实网络请求（保持既有写法不变）
        self._short_link_resolver = short_link_resolver or self._request_short_link

    def resolve(self, raw_url: str) -> ResolvedSource:
        # 支持两种输入：干净的单条链接，或 App「复制链接」拿到的整段分享文案。
        # 前者与后者在这里统一：先从文本里提取真实链接，再走平台识别。
        candidate = first_url_in_text(raw_url)
        if candidate is None:
            if isinstance(raw_url, str) and raw_url.strip():
                raise AppError("INVALID_SOURCE_URL", NO_URL_IN_TEXT_MESSAGE)
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)

        normalized = self._validate_syntax(candidate)
        host = self._host_of(normalized)
        adapter = self._adapter_for(host)
        if adapter is None:
            self._raise_unsupported(host)

        if adapter.is_short_link(host):
            normalized = self._resolve_short_link(normalized, adapter)
            host = self._host_of(normalized)
            target = self._adapter_for(host)
            # 短链只能跳到「同一个平台」的正式域名，避免通过跳转绕过白名单
            if target is None or target.platform != adapter.platform:
                self._raise_unsupported(host)
            adapter = target

        video_id, canonical = adapter.parse(normalized)
        return ResolvedSource(
            platform=adapter.platform,
            url=canonical,
            # 存提取出来的链接（而不是整段分享文案），界面上“原始地址”更干净
            original_url=candidate,
            video_id=video_id,
        )

    def supported_names(self) -> list[str]:
        return [adapter.name for adapter in self.adapters]

    def unsupported_message(self) -> str:
        names = "、".join(self.supported_names())
        return f"当前仅支持{names}链接。你也可以把视频保存到本地后直接上传"

    # ------------------------------------------------------------------ 内部

    def _raise_unsupported(self, host: str) -> None:
        """区分两类拒绝：已知但暂未启用的平台 vs 完全未知的平台。"""
        for domain, name in PENDING_PLATFORM_DOMAINS.items():
            if _is_under(host, {domain}):
                raise AppError(
                    "UNSUPPORTED_PLATFORM",
                    f"暂时还不支持{name}链接，我们正在接入中。你可以先试试"
                    f"{'、'.join(self.supported_names())}链接",
                )
        raise AppError("UNSUPPORTED_PLATFORM", self.unsupported_message())

    def _adapter_for(self, host: str) -> PlatformAdapter | None:
        for adapter in self.adapters:
            if adapter.matches(host) or adapter.is_short_link(host):
                return adapter
        return None

    def _validate_syntax(self, raw_url: str) -> str:
        if not isinstance(raw_url, str):
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        candidate = raw_url.strip()
        if not candidate or len(candidate) > MAX_URL_LENGTH:
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise AppError("INVALID_SOURCE_URL", INVALID_URL_MESSAGE)
        return candidate

    @staticmethod
    def _host_of(url: str) -> str:
        return (urlsplit(url).hostname or "").lower().rstrip(".")

    def _resolve_short_link(self, url: str, adapter: PlatformAdapter) -> str:
        try:
            # 默认解析器需要知道平台（小红书要用移动端 UA），
            # 而外部注入的解析器只收 URL（既有测试就是这么注入的）。
            resolver_function = getattr(self._short_link_resolver, "__func__", None)
            if resolver_function is PlatformService._request_short_link:
                final_url = self._request_short_link(url, adapter)
            else:
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

    def _request_short_link(self, url: str, adapter: PlatformAdapter) -> str:
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
                "User-Agent": adapter.short_link_user_agent or DESKTOP_USER_AGENT,
                "Referer": adapter.homepage or url,
            },
        )
        return response.url
