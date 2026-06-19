"""合规过滤单测 —— 全部使用占位/演示词，不含任何真实敏感词。

红黄线机制（字面 / 夹符 / 谐音 / 红优先于黄）用占位词验证；
真实词库由云端加密下发，不进仓库、不进测试。
"""
from app.services.compliance import check


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
