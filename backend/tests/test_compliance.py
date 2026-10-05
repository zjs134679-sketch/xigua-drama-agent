from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.compliance import check
from app.services.compliance.dictionary import dictionary


@pytest.fixture(autouse=True)
def _use_repo_placeholder_dict(monkeypatch: pytest.MonkeyPatch):
    """强制使用仓库占位词库：本机若存在云端下发的 *.local.txt 会遮蔽演示词，
    导致占位词单测失败。这里忽略 .local，测完恢复。"""
    real_resolve = dictionary._resolve

    def resolve_base_only(base: str):
        return settings.dict_dir / f"{base}.txt"

    monkeypatch.setattr(dictionary, "_resolve", resolve_base_only)
    was_loaded = dictionary.loaded
    dictionary.reload()
    yield
    monkeypatch.setattr(dictionary, "_resolve", real_resolve)
    if was_loaded:
        dictionary.reload()


def test_pass_clean_prompt():
    r = check("雨夜，邮局门口，女主撑伞回望，暖色灯光，电影质感")
    assert r.level == "pass"
    assert not r.blocked


def test_yellow_soft_warning():
    r = check("这段里出现了演示黄线词")
    assert r.level == "yellow"
    assert r.warn


def test_red_literal():
    r = check("这里包含演示红线词")
    assert r.level == "red"
    assert r.blocked


def test_red_evasion_with_separators():
    # 夹符规避：演-示*红线词 归一化后应命中
    r = check("演 示*红—线词")
    assert r.level == "red"


def test_red_homophone_pinyin():
    # 谐音规避：占位词 紫薯布丁 -> zishubuding
    r = check("他点了一份子薯布丁")
    assert r.level == "red"


def test_red_priority_over_yellow():
    # 同时含演示黄线词与演示红线词，应判红线
    r = check("既有演示黄线词，也有演示红线词")
    assert r.level == "red"
