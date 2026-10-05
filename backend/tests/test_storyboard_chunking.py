"""分镜生成：长剧本按场次分块，避免 LLM JSON 截断。"""
from __future__ import annotations

from app.services.agents.storyboard_agent import (
    _parse_storyboards_json,
    _split_script_chunks,
)


def test_split_by_scene_headers():
    # 每场故意写长一点，确保 soft_chars 限制会拆成多块
    pad = "△ 描述句。" * 20
    script = f"""# 第一集

## 场次1 军营·夜
**人物**：阿石
{pad}

## 场次2 大帐·夜
**人物**：将军
{pad}

## 场次3 城门·日
**人物**：士兵
{pad}
"""
    chunks = _split_script_chunks(script, soft_chars=120)
    assert len(chunks) >= 2
    assert any("场次1" in c for c in chunks)
    assert any("场次3" in c or "城门" in c for c in chunks)
    assert all(len(c) <= 120 * 3 for c in chunks)


def test_short_script_single_chunk():
    script = "## 场次1 室内\n△ 一镜到底短戏。"
    chunks = _split_script_chunks(script)
    assert len(chunks) == 1
    assert "室内" in chunks[0]


def test_parse_storyboards_json_ok():
    raw = '{"storyboards":[{"storyboard_number":1,"title":"开场","duration":5}]}'
    shots = _parse_storyboards_json(raw)
    assert len(shots) == 1
    assert shots[0]["title"] == "开场"


def test_parse_storyboards_json_fence_and_truncated():
    fenced = '```json\n{"storyboards":[{"title":"A"}]}\n```'
    assert _parse_storyboards_json(fenced)[0]["title"] == "A"

    try:
        _parse_storyboards_json('{"storyboards":[{"title":"未闭合')
        assert False, "should raise"
    except ValueError as exc:
        assert "截断" in str(exc) or "解析失败" in str(exc)
