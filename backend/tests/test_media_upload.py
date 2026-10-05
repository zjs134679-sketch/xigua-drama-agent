"""本地上传绑定测试（不连 Comfy）。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import Character, Drama, Episode, Prop, Scene, Storyboard
from app.services.media_upload import MediaUploadError, attach_media, save_bytes_to_oss


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_save_bytes_rejects_bad_ext(tmp_path, monkeypatch):
    from app.services import media_upload as mu

    monkeypatch.setattr(mu.settings, "data_dir", tmp_path)
    try:
        save_bytes_to_oss(b"abc", original_name="a.exe", kind="image")
        raise AssertionError("should fail")
    except MediaUploadError as exc:
        assert "图片" in str(exc)


def test_attach_character_and_storyboard(tmp_path, monkeypatch):
    from app.services import media_upload as mu

    monkeypatch.setattr(mu.settings, "data_dir", tmp_path)
    db = make_db()
    d = Drama(title="t", status="draft")
    db.add(d)
    db.flush()
    ch = Character(drama_id=d.id, name="甲")
    sc = Scene(drama_id=d.id, location="街", time="夜", prompt="空街")
    pr = Prop(drama_id=d.id, name="刀")
    ep = Episode(drama_id=d.id, episode_number=1, title="e1")
    db.add_all([ch, sc, pr, ep])
    db.flush()
    sb = Storyboard(episode_id=ep.id, storyboard_number=1, duration=3, title="镜1")
    db.add(sb)
    db.commit()

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r1 = attach_media(db, target_type="character", target_id=ch.id, data=png, original_name="c.png")
    assert r1["image_url"].startswith("/oss/")
    db.refresh(ch)
    assert ch.image_url == r1["image_url"]

    r2 = attach_media(db, target_type="scene", target_id=sc.id, data=png, original_name="s.jpg")
    db.refresh(sc)
    assert sc.image_url == r2["url"]

    r3 = attach_media(db, target_type="prop", target_id=pr.id, data=png, original_name="p.webp")
    db.refresh(pr)
    assert pr.image_url == r3["url"]

    r4 = attach_media(
        db, target_type="storyboard_image", target_id=sb.id, data=png, original_name="shot.png"
    )
    db.refresh(sb)
    assert sb.composed_image == r4["url"]

    # 假视频字节（仅测落盘与字段）
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128
    r5 = attach_media(
        db, target_type="storyboard_video", target_id=sb.id, data=mp4, original_name="v.mp4"
    )
    db.refresh(sb)
    assert sb.video_url == r5["video_url"]
    assert (tmp_path / "oss").is_dir()
    assert any(Path(tmp_path / "oss").iterdir())
    db.close()
