from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.domain import ArtStyle, Asset, Character, Episode, Scene, Storyboard
from app.services.compliance import FilterResult, check
from app.services.compliance import enforce
from app.services.compute import ImageJob, JobResult, get_active_node

FLUX_WORKFLOW = "flux-t2i.api.json"
KONTEXT_WORKFLOW = "kontext-multiref.api.json"
PROTECTION_PROMPT = "no text, no watermark, no logo"


class AssetGenerationError(Exception):
    pass


class ComplianceBlocked(AssetGenerationError):
    def __init__(self, result: FilterResult, enforcement: dict):
        super().__init__("内容触发红线")
        self.result = result
        self.enforcement = enforcement


@dataclass
class GenerationOutcome:
    asset: Asset
    result: JobResult
    compliance: FilterResult
    prompt: str
    workflow: str


def _parts(*values: str | None) -> str:
    return ", ".join(value.strip() for value in values if value and value.strip())


def build_character_prompt(
    character: Character | None,
    custom_prompt: str | None,
    style: ArtStyle | None,
    scene: Scene | None = None,
    action: str | None = None,
    consistent: bool = False,
) -> str:
    identity = None
    if character is not None:
        identity = _parts(character.name, character.appearance)
        if consistent:
            identity = _parts(identity, "appearance kept consistent with the character reference")
    scene_prompt = None
    if scene is not None:
        scene_prompt = _parts(scene.location, scene.prompt)
    return _parts(
        style.prompt_suffix if style else None,
        identity,
        custom_prompt,
        scene_prompt,
        action,
        PROTECTION_PROMPT,
    )


def build_scene_prompt(scene: Scene | None, custom_prompt: str | None, style: ArtStyle | None) -> str:
    return _parts(
        style.prompt_suffix if style else None,
        scene.location if scene else None,
        scene.prompt if scene else None,
        custom_prompt,
        PROTECTION_PROMPT,
    )


def _style(db: Session, art_style_id: int | None) -> ArtStyle | None:
    if art_style_id is None:
        return None
    style = db.get(ArtStyle, art_style_id)
    if style is None:
        raise LookupError("画风不存在")
    return style


def _reference(target: Character | Scene) -> str | None:
    return target.local_path or target.image_url


async def _generate(
    db: Session,
    *,
    prompt: str,
    username: str | None,
    workflow: str,
    references: list[str],
    drama_id: int | None,
    name: str,
    category: str,
    target: Character | Scene | None,
) -> GenerationOutcome:
    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    node = get_active_node(db)
    result = await node.text2image(
        ImageJob(prompt=prompt, workflow=workflow, reference_images=references)
    )
    if result.status != "completed":
        raise AssetGenerationError(result.error or "素材生成失败")

    asset = Asset(
        drama_id=drama_id,
        name=name,
        description=prompt,
        type="image",
        category=category,
        url=result.image_url,
        thumbnail_url=result.image_url,
        local_path=result.image_path,
        mime_type="image/png",
        format="png",
    )
    db.add(asset)
    if target is not None:
        target.image_url = result.image_url
        target.local_path = result.image_path
    db.commit()
    db.refresh(asset)
    return GenerationOutcome(asset, result, compliance, prompt, workflow)


async def generate_character_asset(
    db: Session,
    *,
    character_id: int | None = None,
    custom_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    scene_id: int | None = None,
    action: str | None = None,
) -> GenerationOutcome:
    character = db.get(Character, character_id) if character_id is not None else None
    if character_id is not None and (character is None or character.deleted_at is not None):
        raise LookupError("角色不存在")
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    style = _style(db, art_style_id)

    character_ref = _reference(character) if character else None
    scene_ref = _reference(scene) if scene else None
    consistent = bool(character_ref and scene_ref)
    workflow = KONTEXT_WORKFLOW if consistent else FLUX_WORKFLOW
    references = [scene_ref, character_ref] if consistent else []
    prompt = build_character_prompt(character, custom_prompt, style, scene, action, consistent)
    return await _generate(
        db,
        prompt=prompt,
        username=username,
        workflow=workflow,
        references=[ref for ref in references if ref],
        drama_id=character.drama_id if character else (scene.drama_id if scene else None),
        name=character.name if character else "自定义角色素材",
        category="character",
        target=character,
    )


async def generate_storyboard_image(
    db: Session,
    *,
    storyboard_id: int,
    art_style_id: int | None = None,
    username: str | None = None,
) -> GenerationOutcome:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise LookupError("分镜不存在")
    if not (sb.image_prompt and sb.image_prompt.strip()):
        raise AssetGenerationError("该分镜没有画面提示词，请先生成分镜")
    style = _style(db, art_style_id)
    prompt = _parts(style.prompt_suffix if style else None, sb.image_prompt, PROTECTION_PROMPT)

    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    node = get_active_node(db)
    result = await node.text2image(ImageJob(prompt=prompt, workflow=FLUX_WORKFLOW, reference_images=[]))
    if result.status != "completed":
        raise AssetGenerationError(result.error or "分镜出图失败")

    episode = db.get(Episode, sb.episode_id)
    asset = Asset(
        drama_id=episode.drama_id if episode else None,
        name=sb.title or f"分镜{sb.storyboard_number}",
        description=prompt,
        type="image",
        category="storyboard",
        url=result.image_url,
        thumbnail_url=result.image_url,
        local_path=result.image_path,
        mime_type="image/png",
        format="png",
    )
    db.add(asset)
    sb.composed_image = result.image_url or result.image_path
    sb.status = "image_done"
    db.commit()
    db.refresh(asset)
    return GenerationOutcome(asset, result, compliance, prompt, FLUX_WORKFLOW)


async def generate_scene_asset(
    db: Session,
    *,
    scene_id: int | None = None,
    custom_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
) -> GenerationOutcome:
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    style = _style(db, art_style_id)
    prompt = build_scene_prompt(scene, custom_prompt, style)
    return await _generate(
        db,
        prompt=prompt,
        username=username,
        workflow=FLUX_WORKFLOW,
        references=[],
        drama_id=scene.drama_id if scene else None,
        name=scene.location if scene else "自定义场景素材",
        category="scene",
        target=scene,
    )
