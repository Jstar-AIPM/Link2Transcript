"""请求追踪号（阶段 6 上线适配）。

线上排错的关键：把一次请求里所有日志串起来。做法极简：

1. 中间件为每个请求生成一个短 ``trace_id``，放进 ``ContextVar``；
2. 日志格式里带上它，于是该请求相关的所有日志行都能被检索到；
3. 响应头返回 ``X-Trace-Id``，错误响应体里也带 ``trace_id``（便于用户报错时提供编号）。

不引入第三方 SDK；不记录请求体、逐字稿正文或任何凭据。
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar


_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """生成一个新的追踪号（8 位十六进制，够短且不易撞）。"""
    return uuid.uuid4().hex[:8]


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def get_trace_id() -> str:
    return _trace_id.get()


class TraceIdFilter(logging.Filter):
    """把当前请求的 trace_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True
