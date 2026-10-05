"""分镜中的人物/场景资产识别与关联。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Episode, EpisodeCharacter, Prop, Scene, Storyboard, StoryboardCharacter


@dataclass(frozen=True)
class StoryboardImageReference:
    url: str
    kind: str
    label: str
    source_storyboard_id: int | None = None
    # 本软件资产锁定（用于提示词 @角色名 / @道具名）
    asset_name: str | None = None
    character_id: int | None = None
    prop_id: int | None = None
    is_speaker: bool = False


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
    return storyboard.composed_image or storyboard.first_frame_image or storyboard.last_frame_image


def _continuity_frame(storyboard: Storyboard) -> str | None:
    """动作用衔接帧：优先上一镜视频尾帧，保证运镜连续。"""
    return storyboard.last_frame_image or storyboard.composed_image or storyboard.first_frame_image


def _shot_character_ids(db: Session, storyboard_id: int) -> set[int]:
    return set(db.scalars(select(StoryboardCharacter.character_id).where(
        StoryboardCharacter.storyboard_id == storyboard_id
    )).all())


def _asset_url(row: object) -> str | None:
    """优先 /oss 永久地址（上传/生成都有）；local_path 作兜底。

    上传图可能 local_path 缺后缀，不能优先用坏路径。
    """
    image_url = (getattr(row, "image_url", None) or "").strip()
    local_path = (getattr(row, "local_path", None) or "").strip()
    if image_url.startswith("/oss/") or image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url
    if local_path:
        # 绝对路径：若对应 /oss 文件名可用则转成 /oss
        name = Path(local_path.replace("\\", "/")).name
        if name and ("." in name):
            # 有后缀的本地文件名 → /oss/name（Comfy 读 oss 更稳）
            if image_url:
                return image_url
            return f"/oss/{name}"
        if image_url:
            return image_url
        return local_path
    return image_url or None


def resolve_video_reference_images(
    db: Session,
    storyboard: Storyboard,
    *,
    max_refs: int = 6,
) -> tuple[list[StoryboardImageReference], str]:
    """成片多参考图（r2v）：角色定妆（含上传）+ 场景，不依赖分镜出图。

    顺序（与 <Picture N> 一致，人物优先保证脸）：
    1. 说话人角色定妆
    2. 其余出镜角色定妆（有图的）
    3. 场景环境
    4. 本镜可选补充图
    若分镜未关联角色：从本剧有图角色里按台词/动作点名补全。
    """
    max_refs = max(1, min(int(max_refs or 6), 9))
    characters = list(sync_storyboard_characters(db, storyboard))
    scene = match_storyboard_scene(db, storyboard)

    if storyboard.reference_images is not None:
        try:
            urls = json.loads(storyboard.reference_images)
        except (TypeError, json.JSONDecodeError):
            urls = []
        if isinstance(urls, list) and urls:
            rows = _manual_reference_rows(db, storyboard, [str(u) for u in urls if u])
            # 手动列表若没有角色图，仍把有图关联角色插到前面
            manual_urls = {r.url for r in rows}
            for c in characters:
                u = _asset_url(c)
                if u and u not in manual_urls:
                    rows.insert(0, StoryboardImageReference(u, "character", f"角色：{c.name}"))
                    manual_urls.add(u)
            return rows[:max_refs], "manual+cast"

    # 补全：分镜文本提到的、本剧已有定妆图的角色
    episode = db.get(Episode, storyboard.episode_id)
    if episode is not None:
        all_chars = list(
            db.scalars(
                select(Character).where(
                    Character.drama_id == episode.drama_id,
                    Character.deleted_at.is_(None),
                )
            ).all()
        )
        hay = "\n".join(
            filter(
                None,
                [
                    storyboard.dialogue,
                    storyboard.action,
                    storyboard.title,
                    storyboard.description,
                    storyboard.image_prompt,
                    storyboard.video_prompt,
                ],
            )
        )
        linked_ids = {c.id for c in characters}
        for c in all_chars:
            if c.id in linked_ids:
                continue
            if not _asset_url(c):
                continue
            name = (c.name or "").strip()
            if name and name in hay:
                characters.append(c)
                linked_ids.add(c.id)
        # 仍无角色：塞入本剧最多 3 个有图角色（保证上传图一定能进成片）
        if not any(_asset_url(c) for c in characters):
            with_img = [c for c in all_chars if _asset_url(c)]
            characters = with_img[:3]

    rows: list[StoryboardImageReference] = []
    seen: set[str] = set()

    def _add(url: str | None, kind: str, label: str) -> None:
        if not url or url in seen or len(rows) >= max_refs:
            return
        seen.add(url)
        rows.append(StoryboardImageReference(url, kind, label))

    speaker_id = getattr(storyboard, "speaking_character_id", None)
    ordered_chars: list[Character] = []
    if speaker_id:
        for c in characters:
            if c.id == speaker_id:
                ordered_chars.append(c)
                break
    for c in characters:
        if c not in ordered_chars:
            ordered_chars.append(c)

    # 人物定妆优先（上传/生成的 image_url）
    for c in ordered_chars:
        _add(_asset_url(c), "character", f"角色：{c.name}")

    if scene is not None:
        _add(_asset_url(scene), "scene", f"场景：{scene.location or '环境'}")

    # 仅在「没有角色/场景定妆」时，才用本镜首帧/合成图兜底。
    # 有定妆图时不要再塞 first_frame/composed（常是上一镜尾帧预填），
    # 否则 2 个角色会变成 3～4 张参考，且成片台看起来像多出了一镜图。
    if not rows:
        own = storyboard.composed_image or storyboard.first_frame_image
        if own:
            _add(own, "manual", f"本镜参考：镜头 {storyboard.storyboard_number}")

    return rows, "assets"


def _manual_reference_rows(db: Session, storyboard: Storyboard, urls: list[str]) -> list[StoryboardImageReference]:
    episode = db.get(Episode, storyboard.episode_id)
    characters: list[Character] = []
    scenes: list[Scene] = []
    props: list[Prop] = []
    shots: list[Storyboard] = []
    if episode is not None:
        characters = list(db.scalars(select(Character).where(
            Character.drama_id == episode.drama_id, Character.deleted_at.is_(None)
        )).all())
        scenes = list(db.scalars(select(Scene).where(
            Scene.drama_id == episode.drama_id, Scene.deleted_at.is_(None)
        )).all())
        props = list(db.scalars(select(Prop).where(
            Prop.drama_id == episode.drama_id, Prop.deleted_at.is_(None)
        )).all())
        shots = list(db.scalars(select(Storyboard).where(
            Storyboard.episode_id == episode.id, Storyboard.deleted_at.is_(None)
        )).all())
    speaker_id = storyboard.speaking_character_id
    rows: list[StoryboardImageReference] = []
    for url in urls:
        character = next(
            (
                row for row in characters
                if url in (row.local_path, row.image_url, _asset_url(row) or "")
            ),
            None,
        )
        scene = next(
            (row for row in scenes if url in (row.local_path, row.image_url, _asset_url(row) or "")),
            None,
        )
        prop = next(
            (row for row in props if url in (row.local_path, row.image_url, _asset_url(row) or "")),
            None,
        )
        shot = next((row for row in shots if url in (row.composed_image, row.first_frame_image, row.last_frame_image)), None)
        if character:
            is_speaker = bool(speaker_id and character.id == speaker_id)
            name = character.name or "角色"
            rows.append(StoryboardImageReference(
                url,
                "character",
                f"@{'说话人' if is_speaker else '角色'}：{name}",
                asset_name=name,
                character_id=character.id,
                is_speaker=is_speaker,
            ))
        elif scene:
            loc = scene.location or "场景"
            rows.append(StoryboardImageReference(url, "scene", f"@场景：{loc}", asset_name=loc))
        elif prop:
            pname = prop.name or "道具"
            rows.append(StoryboardImageReference(
                url, "prop", f"@道具：{pname}", asset_name=pname, prop_id=prop.id,
            ))
        elif shot:
            rows.append(StoryboardImageReference(url, "continuity", f"连续镜头 {shot.storyboard_number}", shot.id))
        else:
            rows.append(StoryboardImageReference(url, "manual", "手动参考图"))
    return rows


def match_storyboard_props(db: Session, storyboard: Storyboard, *, max_props: int = 3) -> list[Prop]:
    """从本剧道具资产中，按分镜文本点名匹配（有图优先）。"""
    episode = db.get(Episode, storyboard.episode_id)
    if episode is None:
        return []
    props = list(
        db.scalars(
            select(Prop)
            .where(Prop.drama_id == episode.drama_id, Prop.deleted_at.is_(None))
            .order_by(Prop.id)
        ).all()
    )
    if not props:
        return []
    hay = storyboard_text(storyboard)
    if not hay:
        return []
    hits: list[tuple[int, Prop]] = []
    for prop in props:
        name = (prop.name or "").strip()
        if not name or len(name) < 1:
            continue
        if name not in hay:
            continue
        if not _asset_url(prop):
            continue
        hits.append((hay.find(name), prop))
    hits.sort(key=lambda item: item[0])
    return [prop for _, prop in hits[:max_props]]


def resolve_storyboard_image_references(
    db: Session,
    storyboard: Storyboard,
    *,
    characters: list[Character] | None = None,
    scene: Scene | None = None,
) -> tuple[list[StoryboardImageReference], str]:
    """智能参考：优先本软件人物/场景/道具定妆图，再可选上一镜衔接。

    顺序（自动模式）：
    1. 说话角色定妆图（@说话人）
    2. 其他出镜角色定妆图
    3. 场景资产图
    4. 文本点名的道具资产图
    5. （可选）同段落/连续上一镜，仅作动作衔接，不当身份主参考
    """
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
    # 自动补说话人
    if storyboard.speaking_character_id is None and (storyboard.dialogue or "").strip():
        sid = resolve_speaking_character_id(db, storyboard, characters)
        if sid is not None:
            storyboard.speaking_character_id = sid

    rows: list[StoryboardImageReference] = []
    seen: set[str] = set()

    def _add(
        url: str | None,
        kind: str,
        label: str,
        *,
        source_storyboard_id: int | None = None,
        asset_name: str | None = None,
        character_id: int | None = None,
        prop_id: int | None = None,
        is_speaker: bool = False,
    ) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        rows.append(
            StoryboardImageReference(
                url,
                kind,
                label,
                source_storyboard_id,
                asset_name=asset_name,
                character_id=character_id,
                prop_id=prop_id,
                is_speaker=is_speaker,
            )
        )

    speaker_id = storyboard.speaking_character_id
    ordered_chars: list[Character] = []
    if speaker_id:
        for character in characters:
            if character.id == speaker_id:
                ordered_chars.append(character)
                break
    for character in characters:
        if character not in ordered_chars:
            ordered_chars.append(character)

    # 1–2. 本软件角色定妆（说话人优先）
    for character in ordered_chars:
        url = _asset_url(character)
        if not url:
            continue
        is_speaker = bool(speaker_id and character.id == speaker_id)
        name = (character.name or "角色").strip()
        label = f"@{'说话人' if is_speaker else '角色'}：{name}"
        if is_speaker:
            label = f"@说话人：{name}"
        else:
            label = f"@角色：{name}"
        _add(
            url,
            "character",
            label,
            asset_name=name,
            character_id=character.id,
            is_speaker=is_speaker,
        )

    # 3. 本软件场景资产
    if scene is not None:
        scene_url = _asset_url(scene)
        loc = (scene.location or "场景").strip()
        _add(scene_url, "scene", f"@场景：{loc}", asset_name=loc)

    # 4. 本软件道具资产（文本点名）
    for prop in match_storyboard_props(db, storyboard):
        prop_url = _asset_url(prop)
        pname = (prop.name or "道具").strip()
        _add(prop_url, "prop", f"@道具：{pname}", asset_name=pname, prop_id=prop.id)

    # 5. 可选：同段落/连续上一镜仅作动作衔接（不覆盖身份；无资产时才当环境）
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
    if storyboard.segment_key:
        run = [
            row for row in previous_rows
            if row.segment_key == storyboard.segment_key and _image(row)
        ]
    else:
        cursor = storyboard
        for previous in previous_rows:
            if not _same_continuity_scene(previous, cursor):
                break
            if _image(previous):
                run.append(previous)
            cursor = previous
            break  # 只取紧邻上一镜，避免堆太多旧镜图

    has_asset_env = any(r.kind == "scene" for r in rows)
    has_character_asset = any(r.kind == "character" for r in rows)
    # 已有场景+角色资产时，最多再加 1 张上一镜动作衔接；否则用上一镜补环境
    if run:
        prev = run[0]
        frame = _continuity_frame(prev) or _image(prev)
        if frame and frame not in seen:
            if has_asset_env and has_character_asset:
                _add(
                    frame,
                    "previous",
                    f"动作衔接：镜头 {prev.storyboard_number}",
                    source_storyboard_id=prev.id,
                )
            elif not has_asset_env:
                _add(
                    frame,
                    "previous",
                    f"环境/动作衔接：镜头 {prev.storyboard_number}",
                    source_storyboard_id=prev.id,
                )

    return rows, "auto"


def storyboard_text(storyboard: Storyboard) -> str:
    return "\n".join(
        value for value in (
            storyboard.title, storyboard.action, storyboard.dialogue, storyboard.description,
            storyboard.image_prompt, storyboard.video_prompt,
        ) if value
    )


def resolve_speaking_character_id(db: Session, storyboard: Storyboard, cast: list[Character] | None = None) -> int | None:
    """从台词「名：内容」推断说话人；单人镜且有台词则用该关联角色。"""
    dialogue = (storyboard.dialogue or "").strip()
    if not dialogue:
        return None
    cast = cast or []
    by_name = {c.name: c for c in cast if c.name}
    # 第一句带说话人前缀
    first = dialogue.splitlines()[0].strip() if dialogue else ""
    m = re.match(r"^\s*([^\s:：]{1,12})\s*[:：]\s*", first)
    if m:
        name = m.group(1).strip()
        if name in by_name:
            return by_name[name].id
        # 全剧角色名模糊匹配
        episode = db.get(Episode, storyboard.episode_id)
        if episode is not None:
            all_chars = db.scalars(
                select(Character).where(Character.drama_id == episode.drama_id, Character.deleted_at.is_(None))
            ).all()
            for c in all_chars:
                if c.name and (c.name == name or name in c.name or c.name in name):
                    return c.id
    # 无前缀：关联角色恰好 1 人
    if len(cast) == 1:
        return cast[0].id
    return None


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
    # 计入尚未 flush 的 pending，避免拆镜时 copy + sync 双重插入撞 UNIQUE
    for obj in db.new:
        if isinstance(obj, StoryboardCharacter) and obj.storyboard_id == storyboard.id:
            existing_ids.add(obj.character_id)
    for character_id in position:
        if character_id not in existing_ids:
            db.add(StoryboardCharacter(storyboard_id=storyboard.id, character_id=character_id))
            existing_ids.add(character_id)

    by_id = {row.id: row for row in candidates}
    linked = [by_id[character_id] for character_id in existing_ids if character_id in by_id]
    linked.sort(key=lambda row: (position.get(row.id, 10**9), row.id))

    # 自动补说话人（人工已设则保留）
    if storyboard.speaking_character_id is None and (storyboard.dialogue or "").strip():
        sid = resolve_speaking_character_id(db, storyboard, linked)
        if sid is not None:
            storyboard.speaking_character_id = sid
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
