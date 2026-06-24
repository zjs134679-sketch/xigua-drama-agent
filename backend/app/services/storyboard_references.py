"""分镜中的人物/场景资产识别与关联。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Episode, EpisodeCharacter, Scene, Storyboard, StoryboardCharacter


@dataclass(frozen=True)
class StoryboardImageReference:
    url: str
    kind: str
    label: str
    source_storyboard_id: int | None = None


_CONTINUITY_BREAK_MARKERS = (
    "切到", "转场", "闪回", "回忆", "梦境", "幻想", "与此同时", "另一边",
    "次日", "第二天", "数日后", "多年后", "后来", "夜幕降临", "画外",
    "cut to", "flashback", "dream", "meanwhile", "later", "next day",
)


def _normalise(value: str | None) -> str:
    return re.sub(r"[\s，。！？、；：,.;:!?·_-]+", "", (value or "").lower())


def _time_bucket(value: str | None) -> str | None:
    text = _normalise(value)
    for bucket, markers in (
        ("dawn", ("凌晨", "黎明", "拂晓")),
        ("morning", ("清晨", "早晨", "上午")),
        ("noon", ("中午", "正午")),
        ("afternoon", ("下午", "黄昏", "傍晚")),
        ("night", ("夜", "深夜", "晚上")),
    ):
        if any(marker in text for marker in markers):
            return bucket
    return text or None


def _has_continuity_break(storyboard: Storyboard) -> bool:
    text = "\n".join(filter(None, (
        storyboard.title, storyboard.location, storyboard.time, storyboard.action,
        storyboard.description, storyboard.image_prompt,
    ))).lower()
    return any(marker in text for marker in _CONTINUITY_BREAK_MARKERS)


def _same_continuity_scene(left: Storyboard, right: Storyboard) -> bool:
    if _has_continuity_break(right):
        return False
    same_scene = (
        left.scene_id is not None and right.scene_id is not None and left.scene_id == right.scene_id
    ) or (
        bool(_normalise(left.location)) and _normalise(left.location) == _normalise(right.location)
    )
    if not same_scene:
        return False
    left_time, right_time = _time_bucket(left.time), _time_bucket(right.time)
    return not (left_time and right_time and left_time != right_time)


def _image(storyboard: Storyboard) -> str | None:
    return storyboard.composed_image or storyboard.first_frame_image


def _shot_character_ids(db: Session, storyboard_id: int) -> set[int]:
    return set(db.scalars(select(StoryboardCharacter.character_id).where(
        StoryboardCharacter.storyboard_id == storyboard_id
    )).all())


def _manual_reference_rows(db: Session, storyboard: Storyboard, urls: list[str]) -> list[StoryboardImageReference]:
    episode = db.get(Episode, storyboard.episode_id)
    characters: list[Character] = []
    scenes: list[Scene] = []
    shots: list[Storyboard] = []
    if episode is not None:
        characters = list(db.scalars(select(Character).where(
            Character.drama_id == episode.drama_id, Character.deleted_at.is_(None)
        )).all())
        scenes = list(db.scalars(select(Scene).where(
            Scene.drama_id == episode.drama_id, Scene.deleted_at.is_(None)
        )).all())
        shots = list(db.scalars(select(Storyboard).where(
            Storyboard.episode_id == episode.id, Storyboard.deleted_at.is_(None)
        )).all())
    rows: list[StoryboardImageReference] = []
    for url in urls:
        character = next((row for row in characters if url in (row.local_path, row.image_url)), None)
        scene = next((row for row in scenes if url in (row.local_path, row.image_url)), None)
        shot = next((row for row in shots if url in (row.composed_image, row.first_frame_image)), None)
        if character:
            rows.append(StoryboardImageReference(url, "character", f"角色：{character.name}"))
        elif scene:
            rows.append(StoryboardImageReference(url, "scene", f"场景：{scene.location}"))
        elif shot:
            rows.append(StoryboardImageReference(url, "continuity", f"连续镜头 {shot.storyboard_number}", shot.id))
        else:
            rows.append(StoryboardImageReference(url, "manual", "手动参考图"))
    return rows


def resolve_storyboard_image_references(
    db: Session,
    storyboard: Storyboard,
    *,
    characters: list[Character] | None = None,
    scene: Scene | None = None,
) -> tuple[list[StoryboardImageReference], str]:
    """Route references by story continuity; nullable JSON is the manual override."""
    if storyboard.reference_images is not None:
        try:
            urls = json.loads(storyboard.reference_images)
        except (TypeError, json.JSONDecodeError):
            urls = []
        if not isinstance(urls, list):
            urls = []
        return _manual_reference_rows(db, storyboard, [str(url) for url in urls if url]), "manual"

    characters = characters if characters is not None else sync_storyboard_characters(db, storyboard)
    scene = scene if scene is not None else match_storyboard_scene(db, storyboard)
    previous_rows = db.scalars(
        select(Storyboard)
        .where(
            Storyboard.episode_id == storyboard.episode_id,
            Storyboard.deleted_at.is_(None),
            Storyboard.storyboard_number < storyboard.storyboard_number,
        )
        .order_by(Storyboard.storyboard_number.desc())
    ).all()

    run: list[Storyboard] = []
    cursor = storyboard
    for previous in previous_rows:
        if not _same_continuity_scene(previous, cursor):
            break
        run.append(previous)
        cursor = previous

    rows: list[StoryboardImageReference] = []
    generated = [row for row in reversed(run) if _image(row)]
    anchor = generated[0] if generated else None
    immediate = run[0] if run and _image(run[0]) else None
    if anchor is not None:
        anchor_names = [row.name for row in characters if row.id in _shot_character_ids(db, anchor.id)]
        cast_label = f"（{'+'.join(anchor_names)}）" if anchor_names else ""
        rows.append(StoryboardImageReference(
            _image(anchor) or "", "continuity", f"场景锚点：镜头 {anchor.storyboard_number}{cast_label}", anchor.id
        ))
    if immediate is not None and (anchor is None or immediate.id != anchor.id):
        current_ids = {row.id for row in characters}
        previous_ids = set(db.scalars(select(StoryboardCharacter.character_id).where(
            StoryboardCharacter.storyboard_id == immediate.id
        )).all())
        if current_ids & previous_ids:
            previous_names = [row.name for row in characters if row.id in previous_ids]
            cast_label = f"（{'+'.join(previous_names)}）" if previous_names else ""
            rows.append(StoryboardImageReference(
                _image(immediate) or "", "previous", f"动作衔接：镜头 {immediate.storyboard_number}{cast_label}", immediate.id
            ))

    if not rows and scene is not None and (scene.local_path or scene.image_url):
        rows.append(StoryboardImageReference(
            scene.local_path or scene.image_url or "", "scene", f"场景：{scene.location}"
        ))
    # A successful continuity shot is a stronger identity reference than a
    # separate studio portrait. Add portraits only for cast members not already
    # visible in the selected continuity shots, avoiding face/costume conflicts.
    covered_character_ids: set[int] = set()
    for row in rows:
        if row.source_storyboard_id is not None:
            covered_character_ids.update(_shot_character_ids(db, row.source_storyboard_id))
    for character in characters:
        if character.id in covered_character_ids:
            continue
        url = character.local_path or character.image_url
        if url:
            rows.append(StoryboardImageReference(url, "character", f"角色：{character.name}"))

    deduped: list[StoryboardImageReference] = []
    seen: set[str] = set()
    for row in rows:
        if row.url and row.url not in seen:
            deduped.append(row)
            seen.add(row.url)
    return deduped, "auto"


def storyboard_text(storyboard: Storyboard) -> str:
    return "\n".join(
        value for value in (
            storyboard.title, storyboard.action, storyboard.dialogue, storyboard.description,
            storyboard.image_prompt, storyboard.video_prompt,
        ) if value
    )


def sync_storyboard_characters(db: Session, storyboard: Storyboard) -> list[Character]:
    """保留人工/既有关联，并把当前分镜文本明确提到的人物补入关联表。"""
    episode = db.get(Episode, storyboard.episode_id)
    if episode is None:
        return []
    episode_ids = db.scalars(
        select(EpisodeCharacter.character_id).where(EpisodeCharacter.episode_id == episode.id)
    ).all()
    query = select(Character).where(Character.drama_id == episode.drama_id, Character.deleted_at.is_(None))
    if episode_ids:
        query = query.where(Character.id.in_(episode_ids))
    candidates = db.scalars(query.order_by(Character.id)).all()
    haystack = storyboard_text(storyboard)
    position = {row.id: haystack.find(row.name) for row in candidates if row.name and row.name in haystack}

    existing_ids = set(db.scalars(
        select(StoryboardCharacter.character_id).where(StoryboardCharacter.storyboard_id == storyboard.id)
    ).all())
    for character_id in position:
        if character_id not in existing_ids:
            db.add(StoryboardCharacter(storyboard_id=storyboard.id, character_id=character_id))
            existing_ids.add(character_id)

    by_id = {row.id: row for row in candidates}
    linked = [by_id[character_id] for character_id in existing_ids if character_id in by_id]
    linked.sort(key=lambda row: (position.get(row.id, 10**9), row.id))
    return linked


def match_storyboard_scene(db: Session, storyboard: Storyboard) -> Scene | None:
    if storyboard.scene_id is not None:
        scene = db.get(Scene, storyboard.scene_id)
        if scene is not None and scene.deleted_at is None:
            return scene
    episode = db.get(Episode, storyboard.episode_id)
    if episode is None or not storyboard.location:
        return None
    scenes = db.scalars(
        select(Scene).where(Scene.drama_id == episode.drama_id, Scene.deleted_at.is_(None)).order_by(Scene.id)
    ).all()
    location = storyboard.location.strip()
    exact = next((scene for scene in scenes if scene.location.strip() == location), None)
    if exact is not None:
        storyboard.scene_id = exact.id
        return exact
    partial = next((scene for scene in scenes if scene.location in location or location in scene.location), None)
    if partial is not None:
        storyboard.scene_id = partial.id
    return partial
