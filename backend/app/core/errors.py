from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TaskNotFoundError(AppError):
    def __init__(self) -> None:
        super().__init__("TASK_NOT_FOUND", "未找到该任务", status_code=404)

