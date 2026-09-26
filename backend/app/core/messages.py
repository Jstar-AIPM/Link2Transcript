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
        f"超过 {format_limit(limit_minutes)}上限，暂时无法处理。"
        "请换一个时长更短的视频后重试"
    )


def file_too_long_message(duration_seconds: float, limit_minutes: int) -> str:
    return (
        f"该文件时长约 {format_duration(duration_seconds)}，"
        f"超过 {format_limit(limit_minutes)}上限，暂时无法处理。"
        "请剪辑成更短的片段后重试"
    )


# ---------------------------------------------------------------------
# 转写过程的细粒度阶段文案（阶段 3）
#
# 首字延迟期间（模型加载、音频分析）没有任何片段可展示，必须给出独立文案，
# 否则页面看起来像卡死。这些文案覆盖默认的粗粒度阶段文案。
# ---------------------------------------------------------------------

LOADING_MODEL_MESSAGE = "正在加载语音识别模型"
TRANSCRIBING_MESSAGE = "正在生成逐字稿"
RESUMING_MESSAGE = "正在从上次中断处继续生成逐字稿"


# 转写服务上报的阶段码 → 用户文案。
TRANSCRIPTION_STAGE_MESSAGES: dict[str, str] = {
    "loading_model": LOADING_MODEL_MESSAGE,
    "transcribing": TRANSCRIBING_MESSAGE,
}


def transcribing_progress_message(segment_count: int) -> str:
    """已经产出片段之后，把“已生成 N 段”告诉用户，让过程可感知。"""
    return f"正在生成逐字稿（已生成 {max(0, segment_count)} 段）"


def incomplete_audio_message(actual_seconds: float, declared_seconds: float) -> str:
    return (
        f"获取到的音频不完整（仅 {format_duration(actual_seconds)}，"
        f"视频全长 {format_duration(declared_seconds)}），无法生成完整逐字稿。"
        "你可以改用本地文件上传"
    )
