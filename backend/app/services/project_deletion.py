from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.domain import (
    Asset,
    Character,
    Drama,
    Episode,
    EpisodeCharacter,
    EpisodeScene,
    ImageGeneration,
    Prop,
    Scene,
    Storyboard,
    StoryboardCharacter,
    StoryboardReview,
    VideoGeneration,
    VideoMerge,
)


FILE_MODELS = (
    Drama,
    Episode,
    Character,
    Scene,
    Storyboard,
    ImageGeneration,
    VideoGeneration,
    VideoMerge,
    Prop,
    Asset,
)


def _safe_file_paths(value: object, data_root: Path) -> set[Path]:
    """Extract files under data_root only. Prompts and external URLs are ignored."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        result: set[Path] = set()
        for item in value:
            result.update(_safe_file_paths(item, data_root))
        return result
    if not isinstance(value, str):
        return set()

    raw = value.strip()
    if not raw:
        return set()
    if raw[0] in "[{":
        try:
            return _safe_file_paths(json.loads(raw), data_root)
        except (json.JSONDecodeError, TypeError):
            return set()
    if raw.startswith("/oss/"):
        candidate = data_root / "oss" / Path(raw).name
    elif Path(raw).is_absolute():
        candidate = Path(raw)
    else:
        return set()

    root = data_root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return set()
    return {resolved}


def _row_file_paths(rows: Iterable[object], data_root: Path) -> set[Path]:
    paths: set[Path] = set()
    for row in rows:
        for column in row.__table__.columns:
            paths.update(_safe_file_paths(getattr(row, column.name, None), data_root))
    return paths


def _remaining_file_paths(db: Session, data_root: Path) -> set[Path]:
    protected: set[Path] = set()
    for model in FILE_MODELS:
        protected.update(_row_file_paths(db.scalars(select(model)).all(), data_root))
    return protected


def delete_project(db: Session, drama_id: int, *, data_dir: Path | None = None) -> dict:
    drama = db.get(Drama, drama_id)
    if drama is None:
        raise LookupError("项目不存在")

    data_root = (data_dir or settings.data_dir).resolve()
    episodes = db.scalars(select(Episode).where(Episode.drama_id == drama_id)).all()
    characters = db.scalars(select(Character).where(Character.drama_id == drama_id)).all()
    scenes = db.scalars(select(Scene).where(Scene.drama_id == drama_id)).all()
    props = db.scalars(select(Prop).where(Prop.drama_id == drama_id)).all()
    episode_ids = [row.id for row in episodes]
    character_ids = [row.id for row in characters]
    scene_ids = [row.id for row in scenes]
    prop_ids = [row.id for row in props]
    storyboards = db.scalars(select(Storyboard).where(Storyboard.episode_id.in_(episode_ids))).all()
    storyboard_ids = [row.id for row in storyboards]

    image_conditions = [ImageGeneration.drama_id == drama_id]
    video_conditions = [VideoGeneration.drama_id == drama_id]
    asset_conditions = [Asset.drama_id == drama_id]
    merge_conditions = [VideoMerge.drama_id == drama_id]
    if storyboard_ids:
        image_conditions.append(ImageGeneration.storyboard_id.in_(storyboard_ids))
        video_conditions.append(VideoGeneration.storyboard_id.in_(storyboard_ids))
        asset_conditions.append(Asset.storyboard_id.in_(storyboard_ids))
    if scene_ids:
        image_conditions.append(ImageGeneration.scene_id.in_(scene_ids))
    if character_ids:
        image_conditions.append(ImageGeneration.character_id.in_(character_ids))
    if prop_ids:
        image_conditions.append(ImageGeneration.prop_id.in_(prop_ids))
    if episode_ids:
        asset_conditions.append(Asset.episode_id.in_(episode_ids))
        merge_conditions.append(VideoMerge.episode_id.in_(episode_ids))

    image_generations = db.scalars(select(ImageGeneration).where(or_(*image_conditions))).all()
    video_generations = db.scalars(select(VideoGeneration).where(or_(*video_conditions))).all()
    video_merges = db.scalars(select(VideoMerge).where(or_(*merge_conditions))).all()
    assets = db.scalars(select(Asset).where(or_(*asset_conditions))).all()
    candidates = _row_file_paths(
        [drama, *episodes, *characters, *scenes, *storyboards, *props,
         *image_generations, *video_generations, *video_merges, *assets],
        data_root,
    )

    if storyboard_ids:
        db.execute(delete(StoryboardCharacter).where(StoryboardCharacter.storyboard_id.in_(storyboard_ids)))
    if character_ids:
        db.execute(delete(StoryboardCharacter).where(StoryboardCharacter.character_id.in_(character_ids)))
    if episode_ids:
        db.execute(delete(StoryboardReview).where(StoryboardReview.episode_id.in_(episode_ids)))
        db.execute(delete(EpisodeCharacter).where(EpisodeCharacter.episode_id.in_(episode_ids)))
        db.execute(delete(EpisodeScene).where(EpisodeScene.episode_id.in_(episode_ids)))
    if character_ids:
        db.execute(delete(EpisodeCharacter).where(EpisodeCharacter.character_id.in_(character_ids)))
    if scene_ids:
        db.execute(delete(EpisodeScene).where(EpisodeScene.scene_id.in_(scene_ids)))

    for row in [*image_generations, *video_generations, *video_merges, *assets,
                *storyboards, *props, *scenes, *characters, *episodes, drama]:
        db.delete(row)
    db.commit()

    protected = _remaining_file_paths(db, data_root)
    deleted_files = 0
    skipped_files = 0
    errors: list[str] = []
    for path in sorted(candidates):
        if path in protected:
            skipped_files += 1
            continue
        try:
            if path.is_file():
                path.unlink()
                deleted_files += 1
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")

    return {
        "deleted": True,
        "project_id": drama_id,
        "files_deleted": deleted_files,
        "files_skipped": skipped_files,
        "file_errors": errors,
    }
