import logging

from backend.app.core.trace import TraceIdFilter


def configure_logging() -> None:
    """统一日志格式：时间、级别、模块、**追踪号**、消息。

    追踪号（trace_id）用于线上排错：一次请求相关的所有日志都能用同一个编号串起来。
    日志只记元信息，绝不记录凭据、逐字稿正文或音视频内容。
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s trace=%(trace_id)s %(message)s")
    )
    handler.addFilter(TraceIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
