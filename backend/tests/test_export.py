from datetime import datetime
from pathlib import Path

from backend.app.schemas.task import MediaType, TranscriptResult, TranscriptSegment
from backend.app.services.export_service import ExportService


def test_export_creates_utf8_markdown_txt_and_result(tmp_path: Path):
    service = ExportService(tmp_path / "outputs")
    result = TranscriptResult(
        task_id="efc2e5a2-8557-4662-832b-a91d0301f9dc",
        original_filename="中文音频.wav",
        media_type=MediaType.AUDIO,
        language="zh",
        duration_seconds=3.0,
        text="你好，世界。",
        segments=[TranscriptSegment(start=0, end=3, text="你好，世界。")],
        generated_at=datetime.now().astimezone(),
    )
    markdown, txt, result_json = service.export(result)
    assert "# 中文音频.wav" in markdown.read_text(encoding="utf-8")
    assert "[00:00:00] 你好，世界。" in txt.read_text(encoding="utf-8")
    assert service.load_result(result_json).text == "你好，世界。"

