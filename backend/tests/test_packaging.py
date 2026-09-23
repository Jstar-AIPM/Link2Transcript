"""打包排除清单校验（阶段 6 上线适配）。

线上部署时 veFaaS 会把项目目录打包上传。如果 `.env`（含真实凭据）或 `data/`
（含任务数据）被打进包，就等于把密钥与用户数据一起发布出去。
这个测试把"排除清单是否正确"变成每次 `pytest` 都会执行的一步。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
IGNORE_FILE = REPO_ROOT / ".vefaasignore"


def _patterns() -> list[str]:
    return [
        line.strip()
        for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _ignored(relative_path: str) -> bool:
    """按 gitignore 语义判断：后面的规则覆盖前面的（支持 ! 取反）。"""
    result = False
    for pattern in _patterns():
        negate = pattern.startswith("!")
        body = (pattern[1:] if negate else pattern).rstrip("/")
        if fnmatch.fnmatch(relative_path, body) or fnmatch.fnmatch(relative_path, f"{body}/*"):
            result = not negate
    return result


def test_env_and_data_are_excluded_from_deploy_package():
    for path in (
        ".env",
        "data/tasks/x.json",
        "data/.session/bilibili-cookies.txt",
        ".venv/bin/python",
        "frontend/node_modules/x.js",
        ".vefaas/config.json",
        "__pycache__/module.pyc",
    ):
        assert _ignored(path), f"{path} 必须被排除，否则会把密钥/数据/依赖打进部署包"


def test_runtime_files_are_kept_in_deploy_package():
    for path in (
        "backend/app/main.py",
        "pyproject.toml",
        "uv.lock",
        ".env.example",
        "skill/SKILL.md",
    ):
        assert not _ignored(path), f"{path} 必须保留，否则线上无法启动"
