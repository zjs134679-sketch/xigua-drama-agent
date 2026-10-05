"""画风库预设种子（西瓜原创 art_styles）。"""
from __future__ import annotations

import re

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import Base
from app.models.domain import ArtStyle
from app.services.art_style_seed import load_preset_art_styles, seed_preset_art_styles
from app.services.skill_manager import get_skill, list_art_styles


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_load_preset_art_styles_from_disk():
    presets = load_preset_art_styles()
    assert len(presets) >= 10
    names = {p["name"] for p in presets}
    assert "真人都市写实" in names
    assert "真人古风写实" in names
    assert "国风二次元新国潮" in names
    assert "写实电影感" in names
    urban = next(p for p in presets if p["name"] == "真人都市写实")
    assert urban["skill_key"] == "xg_live_metro"
    assert "真人" in urban["prompt_suffix"] or "都市" in urban["prompt_suffix"]
    assert not re.search(r"[A-Za-z]{6,}", urban["prompt_suffix"] or "")
    assert "禁用" in urban["constraint_manual"] or "必带" in urban["constraint_manual"]


def test_seed_inserts_and_is_idempotent():
    db = _session()
    r1 = seed_preset_art_styles(db)
    assert r1["inserted"] >= 10
    assert r1["total_presets"] >= 10
    count = len(db.scalars(select(ArtStyle)).all())
    assert count == r1["total_presets"] or count >= r1["inserted"]

    r2 = seed_preset_art_styles(db)
    assert r2["inserted"] == 0
    assert len(db.scalars(select(ArtStyle)).all()) == count


def test_seed_fills_empty_manual_only():
    db = _session()
    existing = ArtStyle(name="真人都市写实", prompt_suffix="", constraint_manual="", sort_order=0)
    db.add(existing)
    db.commit()

    result = seed_preset_art_styles(db)
    assert result["inserted"] >= 9
    assert result["updated"] >= 1
    row = db.scalars(select(ArtStyle).where(ArtStyle.name == "真人都市写实")).one()
    assert (row.constraint_manual or "").strip()
    assert (row.prompt_suffix or "").strip()

    # 用户已改的手册不被覆盖
    row.constraint_manual = "用户自定义手册"
    row.prompt_suffix = "custom suffix"
    db.commit()
    seed_preset_art_styles(db)
    row2 = db.scalars(select(ArtStyle).where(ArtStyle.name == "真人都市写实")).one()
    assert row2.constraint_manual == "用户自定义手册"
    assert row2.prompt_suffix == "custom suffix"


def test_skill_manager_lists_art_styles():
    skills = list_art_styles()
    assert len(skills) >= 10
    assert all(s["category"] == "art_style" for s in skills)
    detail = get_skill("xg_live_metro", "art_style")
    assert detail is not None
    assert detail["display_name"] == "真人都市写实"
    assert "content" in detail and len(detail["content"]) > 50


def test_full_xigua_pack_on_disk():
    from app.services.art_style_pack import extract_video_style_tags, load_pack

    pack = load_pack("xg_live_metro")
    assert pack is not None
    assert pack["prefix"] and len(pack["prefix"]) > 400
    files = pack["file_list"]
    assert "constraint.md" in files
    assert "prompts/character.md" in files
    assert "prompts/scene.md" in files
    assert "prompts/prop.md" in files
    assert "prompts/shot_video.md" in files
    assert "direction/storyboard.md" in files
    tags = extract_video_style_tags("xg_live_metro", prefer="zh")
    assert tags and ("都市" in tags or "真人" in tags)
    assert not re.search(r"[A-Za-z]{8,}", tags or "")


def test_seed_upgrades_short_manual_to_prefix():
    db = _session()
    db.add(
        ArtStyle(
            name="真人都市写实",
            prompt_suffix="short",
            constraint_manual="很短的约束",
            sort_order=0,
        )
    )
    db.commit()
    result = seed_preset_art_styles(db, refresh_short_manual=True, write_version=False)
    assert result["updated"] >= 1
    row = db.scalars(select(ArtStyle).where(ArtStyle.name == "真人都市写实")).one()
    assert len(row.constraint_manual or "") > 300


def test_seed_preserves_user_manual():
    """无系统标记的用户手写手册，在非 force 时保留。"""
    db = _session()
    seed_preset_art_styles(db, write_version=False)
    row = db.scalars(select(ArtStyle).where(ArtStyle.name == "写实电影感")).one()
    row.constraint_manual = "这是我自己写的项目专用约束手册，不要覆盖"
    row.prompt_suffix = "我的自定义后缀"
    db.commit()

    seed_preset_art_styles(db, write_version=False)
    row2 = db.scalars(select(ArtStyle).where(ArtStyle.name == "写实电影感")).one()
    assert row2.constraint_manual == "这是我自己写的项目专用约束手册，不要覆盖"
    assert row2.prompt_suffix == "我的自定义后缀"


def test_pack_version_bump_refreshes_system_manual_only(monkeypatch, tmp_path):
    """包版本 bump 时刷新带「西瓜画风约束」的系统手册，不碰用户纯手写。"""
    from app.services import art_style_seed as seed_mod

    ver_file = tmp_path / ".seed_version"
    ver_file.write_text("xg-art-v0\n", encoding="utf-8")
    monkeypatch.setattr(seed_mod, "_SEED_VERSION_FILE", ver_file)
    monkeypatch.setattr(seed_mod, "ART_STYLES_PACK_VERSION", "xg-art-v1-test")

    db = _session()
    # 先按真实磁盘包插入
    seed_preset_art_styles(db, write_version=False)
    system_row = db.scalars(select(ArtStyle).where(ArtStyle.name == "真人都市写实")).one()
    assert "西瓜画风约束" in (system_row.constraint_manual or "")
    # 模拟旧版系统手册（仍带系统标记但内容过时）
    system_row.constraint_manual = "# 西瓜画风约束 · 真人都市写实\n\n旧版系统摘要，应被 bump 刷新\n"
    system_row.prompt_suffix = "旧后缀"
    # 另一条：用户自定义名不在预设列表，不会被碰到；同名预设用户手写无标记
    user_row = db.scalars(select(ArtStyle).where(ArtStyle.name == "写实电影感")).one()
    user_row.constraint_manual = "用户完全自定义的电影感说明，无系统标记"
    user_row.prompt_suffix = "user-only"
    db.commit()

    result = seed_preset_art_styles(db, write_version=True)
    assert result["pack_version_bumped"] is True
    assert result["updated"] >= 1

    system_row2 = db.scalars(select(ArtStyle).where(ArtStyle.name == "真人都市写实")).one()
    assert "旧版系统摘要" not in (system_row2.constraint_manual or "")
    assert "西瓜画风约束" in (system_row2.constraint_manual or "")
    assert len(system_row2.constraint_manual or "") > 100

    user_row2 = db.scalars(select(ArtStyle).where(ArtStyle.name == "写实电影感")).one()
    assert user_row2.constraint_manual == "用户完全自定义的电影感说明，无系统标记"
    assert user_row2.prompt_suffix == "user-only"
    assert ver_file.read_text(encoding="utf-8").strip() == "xg-art-v1-test"
