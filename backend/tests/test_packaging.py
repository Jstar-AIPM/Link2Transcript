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
        # 内部文档目录名带前缀点号，必须与不带点号的写法一起挡住，
        # 否则「上线凭据.md」（含管理员码明文）会被打进部署包。
        "·开发文档/上线凭据.md",
        "开发文档/上线凭据.md",
        "·截图和备忘录/screen.png",
        # 点号个数不固定：真实出现过「··截图反馈/」，旧规则「·截图*/」漏挡
        "··截图反馈/截屏2026-09-28.png",
        "截图反馈/a.png",
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


def test_gitignore_excludes_internal_docs_and_secrets():
    """仓库即将公开：内部文档、截图、密钥、运行数据必须被 .gitignore 挡住。

    这条测试防的是“以后改了忽略规则，把内部文档/凭据又放进去”的回归。
    """
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("data/", "开发文档/", "·开发文档/", "·截图*/", ".env"):
        assert pattern in gitignore, f".gitignore 必须包含 {pattern}"
