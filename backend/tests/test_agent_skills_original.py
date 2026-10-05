"""Agent / 画风 skills：仅西瓜 xg_* 命名与可加载性。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.paths import art_styles_dir
from app.services.agents.script_agent import load_skill
from app.services.skill_manager import list_agent_skills

AGENT_SKILLS_DIR = Path(__file__).resolve().parents[1] / "app" / "services" / "agents" / "skills"


def test_only_xg_agent_skill_dirs():
    dirs = [p.name for p in AGENT_SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert dirs
    assert all(name.startswith("xg_") for name in dirs)
    for required in (
        "xg_script_format",
        "xg_cast_extract",
        "xg_shot_break",
        "xg_voice_match",
        "xg_prompt_grid",
        "xg_shot_audit",
    ):
        assert required in dirs


def test_art_styles_are_xg_prefixed():
    root = art_styles_dir()
    dirs = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert dirs
    assert all(name.startswith("xg_") for name in dirs)


def test_load_skill_xg_only():
    body = load_skill("xg_script_format")
    assert "西瓜" in body
    assert "剧本格式化" in body or "场次" in body
    with pytest.raises(FileNotFoundError):
        load_skill("script_rewriter")
    with pytest.raises(FileNotFoundError):
        load_skill("extractor")
    with pytest.raises(FileNotFoundError):
        load_skill("voice_assigner")


def test_skill_manager_lists_xg_agents():
    skills = list_agent_skills()
    names = {s["name"] for s in skills}
    assert "xg_script_format" in names
    assert all(n.startswith("xg_") for n in names)
