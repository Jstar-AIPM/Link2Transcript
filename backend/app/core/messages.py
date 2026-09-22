from __future__ import annotations


"""用户可见文案的统一构造点。

项目有一条硬要求：所有用户可见文案必须是纯中文、不含技术术语。
把需要跨模块复用的文案集中在这里，避免各处拼字符串导致措辞不一致。
"""


def format_duration(seconds: float) -> str:
    """把秒数说成人话：1.5 小时 / 42 分钟 / 18 秒。"""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} 小时"
    if seconds >= 60:
        return f"{int(seconds // 60)} 分钟"
    return f"{int(seconds)} 秒"


def format_limit(minutes: int) -> str:
    """把上限分钟数说成人话：整小时用小时，否则用分钟。"""
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    return f"{minutes} 分钟"


def video_too_long_message(duration_seconds: float, limit_minutes: int) -> str:
    return (
        f"该视频时长约 {format_duration(duration_seconds)}，"
        f"超过 {format_limit(limit_minutes)}上限。请分段处理，或改用本地文件上传"
    )


def file_too_long_message(duration_seconds: float, limit_minutes: int) -> str:
    return (
        f"该文件时长约 {format_duration(duration_seconds)}，"
        f"超过 {format_limit(limit_minutes)}上限。请分段处理后重试"
    )


def incomplete_audio_message(actual_seconds: float, declared_seconds: float) -> str:
    return (
        f"获取到的音频不完整（仅 {format_duration(actual_seconds)}，"
        f"视频全长 {format_duration(declared_seconds)}），无法生成完整逐字稿。"
        "你可以改用本地文件上传"
    )
