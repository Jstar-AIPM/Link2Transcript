"""验收页（backend/app/static/index.html）的最小静态检查。

这个页面是手写的单文件页面，没有构建流程与类型检查，因此用测试兜住两类低级错误：

1. 脚本里 ``querySelector('#xxx')`` 引用了 HTML 里不存在的元素（改 HTML 忘了改脚本）；
2. 关键契约被误删：增量拉取接口、取消接口、以及“未完成”标注相关元素。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PAGE = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


@pytest.fixture(scope="module")
def page_html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_script_only_references_existing_element_ids(page_html: str):
    declared = set(re.findall(r'id="([^"]+)"', page_html))
    referenced = set(re.findall(r"querySelector\('#([\w-]+)'\)", page_html))
    missing = sorted(referenced - declared)
    assert not missing, f"脚本引用了不存在的元素: {missing}"


def test_page_keeps_incremental_and_cancel_contract(page_html: str):
    # 增量拉取：带 after 游标，不能退化成每次全量拉取
    assert "/segments?after=" in page_html
    assert "next_after" in page_html
    # 取消任务
    assert "/cancel" in page_html
    assert "cancellable" in page_html
    # 失败/取消时的“未完成”标注
    assert 'id="incomplete"' in page_html
    # 刷新后恢复上次任务
    assert "transcript-extractor.active-task-id" in page_html


def test_page_handles_all_terminal_statuses(page_html: str):
    for status in ("succeeded", "failed", "cancelled"):
        assert f"'{status}'" in page_html, f"页面未处理终态 {status}"


def test_page_has_no_auto_play_scroll_that_fights_the_user(page_html: str):
    """自动跟随必须在用户手动上滚时暂停。"""
    assert "setFollowing(false)" in page_html or "setFollowing(distance < 24)" in page_html
    assert "scroll" in page_html
