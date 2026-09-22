from __future__ import annotations

import json
from pathlib import Path

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    TranscriptResult,
    processing_method_label,
    source_type_label,
)


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class ExportService:
    def __init__(self, outputs_dir: Path) -> None:
        self.outputs_dir = outputs_dir
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def export(self, result: TranscriptResult) -> tuple[Path, Path, Path]:
        task_dir = self.outputs_dir / result.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = task_dir / "transcript.md"
        txt_path = task_dir / "transcript.txt"
        result_path = task_dir / "result.json"

        markdown = self._markdown(result)
        txt = self._txt(result)
        try:
            markdown_path.write_text(markdown, encoding="utf-8")
            txt_path.write_text(txt, encoding="utf-8")
            result_path.write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise AppError("EXPORT_FAILED", "逐字稿文件生成失败") from exc
        if not markdown_path.stat().st_size or not txt_path.stat().st_size:
            raise AppError("EXPORT_FAILED", "逐字稿文件生成失败")
        return markdown_path, txt_path, result_path

    def load_result(self, path: Path) -> TranscriptResult:
        try:
            return TranscriptResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AppError("RESULT_UNAVAILABLE", "逐字稿结果暂不可用", status_code=500) from exc

    def _markdown(self, result: TranscriptResult) -> str:
        lines = [
            f"# {result.original_filename}",
            "",
            f"- 来源：{source_type_label(result.source_type)}",
        ]
        if result.source_url:
            lines.append(f"- 原始地址：{result.source_url}")
        lines.extend(
            [
                f"- 处理方式：{processing_method_label(result.processing_method)}",
                f"- 生成时间：{result.generated_at.isoformat()}",
                "",
                "## Transcript",
                "",
            ]
        )
        lines.extend(
            f"[{format_timestamp(segment.start)}] {segment.text}" for segment in result.segments
        )
        return "\n".join(lines).rstrip() + "\n"

    def _txt(self, result: TranscriptResult) -> str:
        return "\n".join(
            f"[{format_timestamp(segment.start)}] {segment.text}" for segment in result.segments
        ).rstrip() + "\n"

