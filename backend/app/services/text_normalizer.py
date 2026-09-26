"""逐字稿文本规范化（路线图 3.9.6）。

Whisper（尤其 small）的中文输出有两个已知问题：

1. **夹杂繁体字**（如「強迫症」「神經」「顆」）；
2. **同音错字**（如「腺苷」被识别成「腺肝」、「ATP」被识别成「BTP」）。

这里在**落盘前**做一次纯规则规范化，让「屏幕上看到的」与「下载的文件」保持一致：

- 繁 → 简：用 zhconv（纯 Python，无编译依赖）；
- 错字：一张小而可扩展的对照表，只做整串替换。

明确不做：不改写语义、不引入 LLM 清洗（那是独立能力，见路线图 3.9.5）。
"""

from __future__ import annotations

from backend.app.schemas.task import TranscriptSegment


#: 常见的同音错字（Whisper 中文实测）。只收「确定是错的、且替换无歧义」的，
#: 避免把正确的词改错。新增时请附上真实出处。
COMMON_ASR_FIXES: tuple[tuple[str, str], ...] = (
    ("腺肝", "腺苷"),
    ("BTP", "ATP"),
)


def to_simplified(text: str) -> str:
    """繁体 → 简体。依赖缺失时原样返回，绝不影响主链路。"""
    if not text:
        return text
    try:
        from zhconv import convert
    except Exception:  # pragma: no cover - 兜底：依赖异常不应让任务失败
        return text
    return convert(text, "zh-cn")


def fix_common_errors(text: str) -> str:
    for wrong, right in COMMON_ASR_FIXES:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def normalize_text(text: str) -> str:
    """规范化顺序很重要：先转简体，再修错字（错字表按简体写法维护）。"""
    if not text:
        return text
    return fix_common_errors(to_simplified(text))


def normalize_segment(segment: TranscriptSegment) -> TranscriptSegment:
    """规范化单个片段；文本没变时返回原对象（避免无谓的复制）。"""
    normalized = normalize_text(segment.text)
    if normalized == segment.text:
        return segment
    return segment.model_copy(update={"text": normalized})
