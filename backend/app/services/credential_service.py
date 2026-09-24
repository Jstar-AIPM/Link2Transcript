"""B 站登录态自检（阶段 6）。

为什么需要它：字幕路径比语音转写**快两个数量级**，而它依赖 B 站登录态。
Cookie 失效时系统会（按设计）自动降级为语音转写 —— 功能不受影响，但速度差很多，
且**在任务日志里才会暴露**。上线后没人会一直盯日志，所以这里主动检查一次：

- 启动后在后台线程里调一次 B 站登录态接口；
- 结果缓存在内存里，并出现在 ``/api/v1/config`` 的 ``bilibili_login_valid`` 字段；
- 失效时日志给出明确告警（并提示"更新 BILIBILI_COOKIE 后重启"）。

不做的事：不阻塞启动、不在请求路径上调外部接口、不打印 Cookie 内容。
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

import requests


logger = logging.getLogger(__name__)

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
CHECK_TIMEOUT_SECONDS = 8

Status = Literal["unknown", "valid", "invalid", "not_configured"]


class BilibiliCredentialService:
    def __init__(self, cookie: str) -> None:
        self.cookie = (cookie or "").strip()
        self._status: Status = "not_configured" if not self.cookie else "unknown"
        self._lock = threading.Lock()

    @property
    def status(self) -> Status:
        with self._lock:
            return self._status

    def verify(self) -> Status:
        """调一次登录态接口。任何异常都视为"未知"（不误报失效）。"""
        if not self.cookie:
            return "not_configured"
        try:
            response = requests.get(
                NAV_URL,
                headers={
                    "Cookie": self.cookie,
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.bilibili.com/",
                },
                timeout=CHECK_TIMEOUT_SECONDS,
            )
            payload = response.json()
        except Exception:  # noqa: BLE001 - 网络异常不能影响启动
            logger.info("bilibili_login_check_skipped reason=network")
            return self._set("unknown")

        valid = bool(payload.get("data", {}).get("isLogin"))
        if valid:
            logger.info("bilibili_login_valid")
            return self._set("valid")

        logger.warning(
            "bilibili_login_invalid —— 字幕路径会自动降级为语音转写（明显更慢）。"
            "请更新 BILIBILI_COOKIE（重新登录后复制 SESSDATA）并重启服务"
        )
        return self._set("invalid")

    def verify_in_background(self) -> threading.Thread:
        """后台执行，避免外部接口拖慢启动。"""
        thread = threading.Thread(target=self.verify, name="bilibili-credential", daemon=True)
        thread.start()
        return thread

    def _set(self, status: Status) -> Status:
        with self._lock:
            self._status = status
            return status
