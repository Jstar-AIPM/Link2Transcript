#!/usr/bin/env python3
"""平台可行性验证探针（阶段 5 前置 Spike，手动运行，不进入自动化测试）。

用途：拿到真实的抖音 / 小红书 / B 站链接后，先在本机确认「yt-dlp 能不能解析、
有没有字幕轨、有没有独立音频流」，再决定要不要验证机房出口的网络条件。

为什么单独写一个脚本：主链路的 `DownloadService` 对平台做了白名单限制
（只认 B 站），验证阶段需要**不受白名单约束**地看 yt-dlp 的原始能力。

用法::

    # 单个或多个链接
    .venv/bin/python scripts/platform_spike.py "https://v.douyin.com/xxxx/" "https://www.xiaohongshu.com/explore/xxxx"

    # 需要登录态时（Cookie 只从环境变量读，不打印、不入库）
    BILIBILI_COOKIE='SESSDATA=...' .venv/bin/python scripts/platform_spike.py <url>

    # 需要代理的出口（例如本地访问受限平台）
    PLATFORM_PROXY=http://127.0.0.1:7890 .venv/bin/python scripts/platform_spike.py <url>

输出：每个链接一行 JSON（不含查询串与任何凭据），便于对比与留档。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

# 让脚本能从项目根目录导入 backend（仅在需要复用常量时）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PLATFORM_DOMAINS = {
    "bilibili.com": "bilibili",
    "b23.tv": "bilibili(short)",
    "douyin.com": "douyin",
    "iesdouyin.com": "douyin",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu(short)",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def guess_platform(host: str) -> str:
    for domain, name in PLATFORM_DOMAINS.items():
        if host == domain or host.endswith(f".{domain}"):
            return name
    return "unknown"


def redact(url: str) -> dict:
    """只保留 host 与 path（去掉可能带 token 的查询串）。"""
    parts = urlsplit(url)
    return {"host": parts.hostname or "", "path": parts.path}


def cookie_file_from_env() -> str | None:
    """把环境变量里的 Cookie 写成 yt-dlp 能用的 Netscape 文件（不打印内容）。"""
    raw = os.getenv("BILIBILI_COOKIE", "").strip()
    if not raw:
        return None
    path = Path("/tmp/platform-spike-cookies.txt")
    lines = ["# Netscape HTTP Cookie File"]
    for pair in raw.split(";"):
        if "=" not in pair:
            continue
        name, _, value = pair.strip().partition("=")
        if not name or not value:
            continue
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return str(path)


def probe(url: str) -> dict:
    import yt_dlp

    target = redact(url)
    result: dict = {
        "input": f"{target['host']}{target['path']}",
        "platform_guess": guess_platform(target["host"]),
        "ok": False,
    }

    options: dict = {
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "skip_download": True,
        # 探测字幕轨（不下载）：为“字幕优先”路径提供依据
        "writesubtitles": True,
        "socket_timeout": int(os.getenv("PLATFORM_SPIKE_TIMEOUT", "25")),
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "ignoreerrors": False,
        "http_headers": dict(BROWSER_HEADERS),
    }
    proxy = os.getenv("PLATFORM_PROXY", "").strip()
    if proxy:
        options["proxy"] = proxy
    cookie_file = cookie_file_from_env()
    if cookie_file:
        options["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - 这是诊断工具，任何异常都要原样记录
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:400]
        return result

    if not isinstance(info, dict):
        result["error_type"] = "UnexpectedInfo"
        result["error"] = "extract_info 未返回字典"
        return result

    if info.get("_type") == "playlist" or info.get("entries") is not None:
        entries = info.get("entries") or []
        info = next((item for item in entries if isinstance(item, dict)), info)

    formats = info.get("formats") or []
    result.update(
        {
            "ok": True,
            "extractor": info.get("extractor"),
            "video_id": info.get("id"),
            "title": (info.get("title") or "")[:80],
            "duration_seconds": info.get("duration"),
            "is_live": info.get("is_live"),
            "subtitles_langs": sorted((info.get("subtitles") or {}).keys()),
            "automatic_captions_langs": sorted((info.get("automatic_captions") or {}).keys()),
            "format_count": len(formats),
            "has_audio_only": any(
                f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")
                for f in formats
                if isinstance(f, dict)
            ),
            "ext": info.get("ext"),
        }
    )
    return result


def main(argv: list[str]) -> int:
    urls = [item for item in argv if item.strip()]
    if not urls:
        print(__doc__)
        return 2
    if os.getenv("BILIBILI_COOKIE", "").strip():
        print("# 已从环境变量注入 B 站 Cookie（内容不打印）", file=sys.stderr)
    for url in urls:
        print(json.dumps(probe(url), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
