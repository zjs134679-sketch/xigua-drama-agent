from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.domain import (
    Asset,
    Character,
    Drama,
    Episode,
    EpisodeCharacter,
    ImageGeneration,
    Scene,
    Storyboard,
    StoryboardCharacter,
)
from app.services.project_deletion import delete_project


def test_delete_project_removes_records_and_only_unshared_data_files(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    oss = tmp_path / "oss"
    oss.mkdir()
    owned = oss / "owned.png"
    shared = oss / "shared.png"
    outside = tmp_path.parent / "must-stay.png"
    owned.write_bytes(b"owned")
    shared.write_bytes(b"shared")
    outside.write_bytes(b"outside")

    doomed = Drama(title="待删除")
    keeper = Drama(title="保留")
    db.add_all([doomed, keeper])
    db.flush()
    episode = Episode(drama_id=doomed.id, episode_number=1, title="第一集")
    character = Character(drama_id=doomed.id, name="甲", local_path=str(shared))
    scene = Scene(drama_id=doomed.id, location="城门", time="夜", prompt="empty")
    db.add_all([episode, character, scene])
    db.flush()
    storyboard = Storyboard(episode_id=episode.id, storyboard_number=1, composed_image="/oss/owned.png")
    db.add(storyboard)
    db.flush()
    db.add_all([
        EpisodeCharacter(episode_id=episode.id, character_id=character.id),
        StoryboardCharacter(storyboard_id=storyboard.id, character_id=character.id),
        ImageGeneration(drama_id=doomed.id, storyboard_id=storyboard.id, local_path=str(owned)),
        Asset(drama_id=doomed.id, local_path=str(outside)),
        Asset(drama_id=keeper.id, local_path=str(shared)),
    ])
    db.commit()

    result = delete_project(db, doomed.id, data_dir=tmp_path)

    assert result["deleted"] is True
    assert result["files_deleted"] == 1
    assert not owned.exists()
    assert shared.exists()
    assert outside.exists()
    assert db.get(Drama, doomed.id) is None
    assert db.get(Drama, keeper.id) is not None
    assert db.scalars(select(Episode).where(Episode.drama_id == doomed.id)).all() == []
    assert db.scalars(select(Storyboard)).all() == []
    assert db.scalars(select(EpisodeCharacter)).all() == []
    assert db.scalars(select(StoryboardCharacter)).all() == []
    db.close()
