"""B 站请求会话（阶段 6）：统一请求头、设备 Cookie 与流式下载。

被两条链路共用：

- ``BilibiliApiService``（线上默认，走 api.bilibili.com）；
- ``DownloadService``（yt-dlp 后备，抓 HTML 页面）。

为什么要单独抽出来：

1. **设备 Cookie**：B 站对机房 IP 容易直接 412，带上首页发放的 ``buvid3`` 等
   设备 Cookie 是常见缓解手段；取不到也不影响主流程；
2. **浏览器头**：UA / Referer 缺失也容易被风控；
3. 凭据只留在内存与受控 cookie 文件里，日志不打印值。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import requests


logger = logging.getLogger(__name__)

BILIBILI_HOME_URL = "https://www.bilibili.com/"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": BILIBILI_HOME_URL,
    "Accept-Language": "zh-CN,zh;q=0.9",
}
DEVICE_COOKIE_NAMES = ("buvid3", "buvid4", "b_nut")
DEVICE_COOKIE_TTL_SECONDS = 6 * 3600


class BilibiliSession:
    def __init__(
        self,
        *,
        cookie: str = "",
        cookie_file_dir: Path | None = None,
        harvest_device_cookies: bool = False,
    ) -> None:
        self.cookie = (cookie or "").strip()
        self.cookie_file_dir = cookie_file_dir
        # 默认关闭：这是一个"锦上添花"的风控缓解项，且会产生一次外部请求。
        # 运行环境（main.py）显式开启；离线测试保持关闭。
        self.harvest_device_cookies = harvest_device_cookies
        self._device_cookies: dict[str, str] = {}
        self._device_cookies_at = 0.0
        self._device_cookie_lock = threading.Lock()

    # ------------------------------------------------------------------ 请求

    @property
    def headers(self) -> dict[str, str]:
        headers = dict(BROWSER_HEADERS)
        cookie_header = self.cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    def cookie_header(self) -> str:
        """环境变量里的凭据 + 首页发放的设备 Cookie。"""
        pairs = self.cookie_pairs()
        return "; ".join(f"{name}={value}" for name, value in pairs)

    def cookie_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for part in self.cookie.split(";"):
            name, separator, value = part.strip().partition("=")
            name, value = name.strip(), value.strip()
            if separator and name and value:
                pairs.append((name, value))
                seen.add(name)
        for name, value in self.device_cookies().items():
            if name not in seen:
                pairs.append((name, value))
        return pairs

    def device_cookies(self) -> dict[str, str]:
        if not self.harvest_device_cookies:
            return {}
        now = time.monotonic()
        with self._device_cookie_lock:
            if self._device_cookies and now - self._device_cookies_at < DEVICE_COOKIE_TTL_SECONDS:
                return dict(self._device_cookies)
        harvested: dict[str, str] = {}
        try:
            response = requests.get(BILIBILI_HOME_URL, headers=self.headers, timeout=8)
            harvested = {
                name: value
                for name in DEVICE_COOKIE_NAMES
                if (value := response.cookies.get(name))
            }
        except Exception:  # noqa: BLE001 - 增强项，失败不影响主流程
            logger.info("bilibili_device_cookie_skipped")
        with self._device_cookie_lock:
            if harvested:
                self._device_cookies = harvested
                self._device_cookies_at = now
            return dict(self._device_cookies)

    def get_json(self, url: str, params: dict | None = None, *, timeout: int = 20) -> dict:
        response = requests.get(url, params=params, headers=self.headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str, *, timeout: int = 20) -> str:
        response = requests.get(url, headers=self.headers, timeout=timeout)
        response.raise_for_status()
        return response.text

    def download(self, url: str, destination: Path, *, timeout: int = 300) -> int:
        """流式下载到文件，返回字节数（不把整个文件读进内存）。"""
        written = 0
        with requests.get(
            url, headers=self.headers, timeout=timeout, stream=True
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        handle.write(chunk)
                        written += len(chunk)
        return written

    # ------------------------------------------------------------ cookie 文件

    def write_netscape_cookie_file(self, path: Path) -> Path | None:
        """写成 yt-dlp 能用的 Netscape cookie 文件（domain 覆盖两个子域）。"""
        if not self.cookie:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        lines = [
            "# Netscape HTTP Cookie File",
            "# 由本项目从 BILIBILI_COOKIE 生成，请勿提交到版本控制或外传",
        ]
        for name, value in self.cookie_pairs():
            lines.append("\t".join([".bilibili.com", "TRUE", "/", "TRUE", "0", name, value]))
        content = "\n".join(lines) + "\n"
        try:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != content:
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)
        except OSError:
            logger.warning("cookie_file_write_failed", exc_info=True)
            return None
        return path
