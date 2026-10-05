"""风格契约 / 矩阵 / 注入前缀 — 西瓜原创，无外部 Skill 依赖。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import ArtStyle, Drama
from app.services.style_composer import (
    StyleConflictError,
    compose,
    save_bible,
    validate_bible_dict,
)
from app.services.style_contract import (
    STAGE_IDENTITY,
    STAGE_SHOT_IMAGE,
    StyleContract,
    check_compat,
    extract_anchors_from_manual,
    load_platform,
    load_style_matrix,
)


def test_platform_and_matrix_load():
    p = load_platform()
    assert int(p.get("max_shot_duration_sec") or 0) == 5
    m = load_style_matrix()
    assert "visual_families" in m
    assert "matrix" in m


def test_compat_whimsy_forbids_cine():
    level, msg = check_compat("pace_whimsy", "xg_cine_real")
    assert level == "forbid"
    assert msg


def test_compat_dense_allows_cine():
    level, _ = check_compat("pace_dense", "xg_cine_real")
    assert level in ("recommend", "allow")


def test_extract_anchors():
    manual = """
## 2. 出图必带词
`电影剧照, 写实光影, 统一色调, 景深层次, 无水印`
"""
    anchors = extract_anchors_from_manual(manual)
    assert "电影剧照" in anchors
    assert len(anchors) >= 3


def test_system_prefix_truncated():
    c = StyleContract(
        must_include=["a"] * 50,
        pacing_hint="x" * 500,
        stage=STAGE_SHOT_IMAGE,
    )
    text = c.system_prefix(max_chars=200)
    assert len(text) <= 200


def test_identity_vs_shot_notes_differ():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    style = ArtStyle(
        name="写实电影感",
        prompt_suffix="电影剧照写实",
        constraint_manual="`电影剧照, 写实光影, 统一色调`\n## 6. 禁用清单\n水印\nUI边框",
    )
    db.add(style)
    drama = Drama(title="测", style="写实电影感")
    db.add(drama)
    db.flush()
    save_bible(
        drama,
        {
            "version": 1,
            "art_style_id": style.id,
            "visual_name": style.name,
            "visual_pack": "xg_cine_real",
            "pacing_profile": "pace_balanced",
            "aspect": "9:16",
        },
    )
    db.commit()

    id_c = compose(db=db, drama=drama, stage=STAGE_IDENTITY, art_style=style)
    sh_c = compose(db=db, drama=drama, stage=STAGE_SHOT_IMAGE, art_style=style)
    assert "定装" in id_c.stage_notes
    assert "分镜图" in sh_c.stage_notes
    assert id_c.stage_notes != sh_c.stage_notes
    assert id_c.must_include  # from manual or pack


def test_validate_forbid_raises():
    try:
        validate_bible_dict(
            {
                "pacing_profile": "pace_whimsy",
                "visual_pack": "xg_cine_real",
            }
        )
        assert False, "should raise"
    except StyleConflictError:
        pass


def test_scan_excludes():
    c = StyleContract(must_exclude=["水印", "字幕条"])
    assert c.scan_excludes("画面有水印和logo") == ["水印"]
    assert c.missing_includes("电影剧照 写实") == []  # no must_include
