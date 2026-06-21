from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathlib import Path

from app.models.domain import ArtStyle, Asset, Character, Episode, ImageGeneration, Prop, Scene, Storyboard
from app.services.compliance import FilterResult, check
from app.services.compliance import enforce
from app.services.compute import ImageJob, JobResult, get_node

FLUX_WORKFLOW = "flux-t2i.api.json"
KONTEXT_WORKFLOW = "kontext-multiref.api.json"
PROTECTION_PROMPT = "no text, no watermark, no logo"
CHARACTER_PORTRAIT_PROMPT = (
    "solo, single character, plain solid color background, clean simple background, character centered"
)
CHARACTER_NEGATIVE_PROMPT = (
    "text, words, letters, characters, watermark, signature, logo, stamp, subtitles, UI, border, "
    "multiple people, crowd, landscape, complex background, building, blurry, low quality, "
    "animal head, animal ears, furry, anthro, extra hands, extra arms, extra fingers, "
    "mutated hands, deformed hands, fused fingers, too many fingers, "
    "extra limbs, extra legs, conjoined, multiple heads, two heads"
)
GENERAL_NEGATIVE_PROMPT = (
    "text, watermark, signature, logo, stamp, subtitles, UI, border, blurry, low quality"
)
RESOLUTION_PRESETS: dict[str, tuple[int, int]] = {
    "portrait_768x1024": (768, 1024),
    "square_1024x1024": (1024, 1024),
    "landscape_1024x576": (1024, 576),
    "hd_portrait_896x1152": (896, 1152),
}
DEFAULT_RESOLUTION = {
    "character": "portrait_768x1024",
    "scene": "landscape_1024x576",
    "prop": "landscape_1024x576",
    "storyboard": "landscape_1024x576",
}


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


def resolve_resolution(resolution: str | None, category: str) -> tuple[str, int, int]:
    key = resolution if resolution in RESOLUTION_PRESETS else DEFAULT_RESOLUTION[category]
    width, height = RESOLUTION_PRESETS[key]
    return key, width, height


VIEW_TYPE_PROMPTS = {
    "full_body": "full body shot, from head to toe fully visible, showing complete outfit and body shape, standing frontal pose",
    "headshot": "headshot close-up, face dominates the frame, sharp facial features, shoulders and above, portrait photo style",
    "side": "side profile shot, showing character silhouette from the side, side face and body contour",
}


def build_character_prompt(
    character: Character | None,
    custom_prompt: str | None,
    style: ArtStyle | None,
    scene: Scene | None = None,
    action: str | None = None,
    consistent: bool = False,
    view_type: str = "full_body",
) -> str:
    identity = None
    if character is not None:
        identity = _parts(character.name, character.appearance)
        if consistent:
            identity = _parts(identity, "外观与参考图保持一致")
    scene_prompt = None
    if scene is not None:
        scene_prompt = _parts(scene.location, scene.prompt)
    # view_hint 与 CHARACTER_PORTRAIT_PROMPT 改到 generate_character_asset 统一追加（对所有提示词来源生效）
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


def build_prop_prompt(prop: Prop | None, custom_prompt: str | None, style: ArtStyle | None) -> str:
    return _parts(
        style.prompt_suffix if style else None,
        prop.name if prop else None,
        prop.type if prop else None,
        prop.description if prop else None,
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


def _reference(target: Character | Scene | Prop) -> str | None:
    return target.local_path or target.image_url


def _generation_target_field(category: str) -> str:
    return {
        "character": "character_id",
        "scene": "scene_id",
        "prop": "prop_id",
        "storyboard": "storyboard_id",
    }[category]


def _record_generation(
    db: Session,
    *,
    category: str,
    target: Character | Scene | Prop | Storyboard | None,
    drama_id: int | None,
    prompt: str,
    negative: str,
    node,
    result: JobResult,
    resolution: str,
    width: int,
    height: int,
    seed: int,
) -> ImageGeneration:
    target_field = _generation_target_field(category)
    oss_url = "/oss/" + Path(result.image_path).name if result.image_path else result.image_url
    values = {
        target_field: target.id if target is not None else None,
        "drama_id": drama_id,
        "image_type": category,
        "provider": node.type,
        "prompt": prompt,
        "negative_prompt": negative,
        "model": result.meta.get("model") or getattr(node, "model", None) or node.type,
        "size": resolution,
        "seed": seed,
        "image_url": oss_url,
        "local_path": result.image_path,
        "status": "completed",
        "width": width,
        "height": height,
        "completed_at": datetime.utcnow(),
    }
    generation = ImageGeneration(**values)
    db.add(generation)
    db.flush()

    if target is not None:
        target_column = getattr(ImageGeneration, target_field)
        older = db.scalars(
            select(ImageGeneration)
            .where(target_column == target.id, ImageGeneration.status == "completed")
            .order_by(ImageGeneration.id.desc())
            .offset(3)
        ).all()
        for row in older:
            db.delete(row)
    return generation


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
    target: Character | Scene | Prop | None,
    negative: str,
    node_id: int | None,
    resolution: str | None,
) -> GenerationOutcome:
    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    resolution_key, width, height = resolve_resolution(resolution, category)
    seed = random.randint(1, 2**31 - 1)
    node = get_node(db, node_id)
    result = await node.text2image(
        ImageJob(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            seed=seed,
            workflow=workflow,
            reference_images=references,
        )
    )
    if result.status != "completed":
        raise AssetGenerationError(result.error or "素材生成失败")

    oss_url = "/oss/" + Path(result.image_path).name if result.image_path else result.image_url
    asset = Asset(
        drama_id=drama_id,
        name=name,
        description=prompt,
        type="image",
        category=category,
        url=oss_url,
        thumbnail_url=oss_url,
        local_path=result.image_path,
        mime_type="image/png",
        format="png",
    )
    db.add(asset)
    if target is not None:
        target.image_url = oss_url
        target.local_path = result.image_path
    generation = _record_generation(
        db,
        category=category,
        target=target,
        drama_id=drama_id,
        prompt=prompt,
        negative=negative,
        node=node,
        result=result,
        resolution=resolution_key,
        width=width,
        height=height,
        seed=seed,
    )
    asset.width = width
    asset.height = height
    asset.image_gen_id = generation.id
    db.commit()
    db.refresh(asset)
    return GenerationOutcome(asset, result, compliance, prompt, workflow)


async def generate_character_asset(
    db: Session,
    *,
    character_id: int | None = None,
    full_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    scene_id: int | None = None,
    action: str | None = None,
    node_id: int | None = None,
    resolution: str | None = None,
    extra: str | None = None,
    view_type: str = "full_body",
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

    # 提示词优先级：用户传入的完整提示词 > 已存可编辑提示词 > 自动拼接
    if full_prompt and full_prompt.strip():
        base_prompt = full_prompt.strip()
    elif character is not None and character.image_prompt and character.image_prompt.strip():
        base_prompt = character.image_prompt.strip()
    else:
        base_prompt = build_character_prompt(character, None, style, scene, action, consistent, view_type)
    if character is not None:
        character.image_prompt = base_prompt  # 附加指令仅用于本次生成，不污染可编辑提示词
        character.view_type = view_type  # 记录本次出图视角
    # 始终把「纯背景/单人/无文字」追加到正向（flux cfg=1 负向无效），对所有提示词来源都生效
    view_hint = VIEW_TYPE_PROMPTS.get(view_type, VIEW_TYPE_PROMPTS["full_body"])
    prompt = _parts(base_prompt, view_hint, CHARACTER_PORTRAIT_PROMPT, extra)

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
        negative=CHARACTER_NEGATIVE_PROMPT,
        node_id=node_id,
        resolution=resolution,
    )


async def generate_storyboard_image(
    db: Session,
    *,
    storyboard_id: int,
    full_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    node_id: int | None = None,
    resolution: str | None = None,
    extra: str | None = None,
) -> GenerationOutcome:
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.deleted_at is not None:
        raise LookupError("分镜不存在")
    if full_prompt and full_prompt.strip():
        sb.image_prompt = full_prompt.strip()  # 持久化用户编辑的提示词
    if not (sb.image_prompt and sb.image_prompt.strip()):
        raise AssetGenerationError("该分镜没有画面提示词，请先生成分镜")
    style = _style(db, art_style_id)
    prompt = _parts(style.prompt_suffix if style else None, sb.image_prompt, PROTECTION_PROMPT, extra)

    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    resolution_key, width, height = resolve_resolution(resolution, "storyboard")
    seed = random.randint(1, 2**31 - 1)
    node = get_node(db, node_id)
    result = await node.text2image(
        ImageJob(
            prompt=prompt,
            negative=GENERAL_NEGATIVE_PROMPT,
            width=width,
            height=height,
            seed=seed,
            workflow=FLUX_WORKFLOW,
            reference_images=[],
        )
    )
    if result.status != "completed":
        raise AssetGenerationError(result.error or "分镜出图失败")

    episode = db.get(Episode, sb.episode_id)
    oss_url = "/oss/" + Path(result.image_path).name if result.image_path else result.image_url
    asset = Asset(
        drama_id=episode.drama_id if episode else None,
        name=sb.title or f"分镜{sb.storyboard_number}",
        description=prompt,
        type="image",
        category="storyboard",
        url=oss_url,
        thumbnail_url=oss_url,
        local_path=result.image_path,
        mime_type="image/png",
        format="png",
    )
    db.add(asset)
    sb.composed_image = oss_url
    sb.status = "image_done"
    generation = _record_generation(
        db,
        category="storyboard",
        target=sb,
        drama_id=episode.drama_id if episode else None,
        prompt=prompt,
        negative=GENERAL_NEGATIVE_PROMPT,
        node=node,
        result=result,
        resolution=resolution_key,
        width=width,
        height=height,
        seed=seed,
    )
    asset.width = width
    asset.height = height
    asset.image_gen_id = generation.id
    db.commit()
    db.refresh(asset)
    return GenerationOutcome(asset, result, compliance, prompt, FLUX_WORKFLOW)


async def generate_scene_asset(
    db: Session,
    *,
    scene_id: int | None = None,
    full_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    node_id: int | None = None,
    resolution: str | None = None,
    extra: str | None = None,
) -> GenerationOutcome:
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    style = _style(db, art_style_id)

    if full_prompt and full_prompt.strip():
        base_prompt = full_prompt.strip()
    elif scene is not None and scene.prompt and scene.prompt.strip():
        base_prompt = scene.prompt.strip()
    else:
        base_prompt = build_scene_prompt(scene, None, style)
    if scene is not None:
        scene.prompt = base_prompt  # 附加指令仅用于本次生成，不污染可编辑提示词
    prompt = _parts(base_prompt, extra)

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
        negative=GENERAL_NEGATIVE_PROMPT,
        node_id=node_id,
        resolution=resolution,
    )


async def generate_prop_asset(
    db: Session,
    *,
    prop_id: int | None = None,
    full_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    node_id: int | None = None,
    resolution: str | None = None,
    extra: str | None = None,
) -> GenerationOutcome:
    prop = db.get(Prop, prop_id) if prop_id is not None else None
    if prop_id is not None and (prop is None or prop.deleted_at is not None):
        raise LookupError("道具不存在")
    style = _style(db, art_style_id)

    if full_prompt and full_prompt.strip():
        base_prompt = full_prompt.strip()
    elif prop is not None and prop.prompt and prop.prompt.strip():
        base_prompt = prop.prompt.strip()
    else:
        base_prompt = build_prop_prompt(prop, None, style)
    if prop is not None:
        prop.prompt = base_prompt  # 附加指令仅用于本次生成，不污染可编辑提示词
    prompt = _parts(base_prompt, extra)

    return await _generate(
        db,
        prompt=prompt,
        username=username,
        workflow=FLUX_WORKFLOW,
        references=[],
        drama_id=prop.drama_id if prop else None,
        name=prop.name if prop else "自定义道具素材",
        category="prop",
        target=prop,
        negative=GENERAL_NEGATIVE_PROMPT,
        node_id=node_id,
        resolution=resolution,
    )
