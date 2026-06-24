from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathlib import Path

from app.models.domain import (
    ArtStyle, Asset, Character, Drama, Episode, ImageGeneration, Prop, Scene,
    Storyboard, StoryboardCharacter,
)
from app.services.compliance import FilterResult, check
from app.services.compliance import enforce
from app.services.compute import ImageJob, JobResult, get_node
from app.services.storyboard_references import (
    match_storyboard_scene,
    resolve_storyboard_image_references,
    sync_storyboard_characters,
)

FLUX_WORKFLOW = "flux-t2i.api.json"
KONTEXT_WORKFLOW = "kontext-multiref.api.json"
PROTECTION_PROMPT = "画面中不要出现文字、水印、logo、字幕、边框、UI"
CHARACTER_PORTRAIT_PROMPT = (
    "单个角色，浅灰色干净影棚背景，背景简洁无杂物，角色居中，适合作为后续分镜参考图"
)
CHARACTER_NEGATIVE_PROMPT = (
    "文字，水印，签名，logo，印章，字幕，UI，边框，不同人物，额外角色，人群，复杂背景，建筑背景，模糊，低质量，"
    "动物头，动物耳朵，兽人，多余的手，多余的手臂，多余的手指，畸形手，融合手指，手指过多，"
    "多余肢体，多余腿，身体粘连，多个头，双头，现代军装，现代服装，拉链，口袋，徽章，肩章，迷彩，枪械"
)
GENERAL_NEGATIVE_PROMPT = (
    "文字，水印，签名，logo，印章，字幕，UI，边框，模糊，低质量"
)
SCENE_NEGATIVE_PROMPT = (
    GENERAL_NEGATIVE_PROMPT
    + "，人物，人像，角色，士兵，人群，人体轮廓，前景主体"
)
STORYBOARD_NEGATIVE_PROMPT = (
    GENERAL_NEGATIVE_PROMPT
    + "，重复角色，克隆脸，额外主角，人物排队站成一排，前景人物过大，主要角色过小，身体比例不一致，身体粘连，肢体融合，"
    "错误脸，身份互换，畸形手"
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
    "turnaround": (
        "角色四视图设定图，同一张图里包含正面、三分之四侧面、纯侧面、背面；每个视图必须是同一角色、同一张脸、同一发型、同一套服装；"
        "中性站姿，全身从头到脚完整可见"
    ),
    "full_body": "全身正面设定照，从头到脚完整可见，清楚展示完整服装、身材比例和站姿",
    "headshot": "头像特写，肩部以上，脸部占主要画面，五官清晰，适合做人脸参考",
    "side": "侧面设定照，清楚展示侧脸轮廓、发型轮廓和身体侧面比例",
}


XIGUA_CHARACTER_SHEET_BASE = (
    "西瓜短剧写实人物设定图，专业真人选角参考照，短剧写实摄影风格，电影感真实质感，"
    "不要动漫风，不要卡通风，不要游戏渲染，自然人体比例，自然表情，可信的人体结构，"
    "高细节，清晰对焦，干净影棚光，浅灰色纯净背景"
)


def _contains_any(source: str, terms: tuple[str, ...]) -> bool:
    return any(term in source for term in terms)


def character_gender_hint(character: Character | None) -> str:
    source = _parts(
        character.name if character else None,
        character.role if character else None,
        character.appearance if character else None,
        character.description if character else None,
    )
    if _contains_any(source, ("女性", "女人", "女孩", "少女", "母亲", "妻子", "小姐", "姑娘", "她")):
        return "女性角色"
    if _contains_any(source, ("男性", "男人", "男孩", "少年", "老头", "老人", "父亲", "丈夫", "将军", "士兵", "他")):
        return "男性角色"
    return "角色"


def character_age_hint(character: Character | None) -> str:
    source = _parts(
        character.role if character else None,
        character.appearance if character else None,
        character.description if character else None,
    )
    if _contains_any(source, ("少年", "少女", "十几岁")):
        return "少年/少女，脸部年轻"
    if _contains_any(source, ("青年", "年轻", "二十", "20")):
        return "青年"
    if _contains_any(source, ("中年", "四十", "40", "五十", "50")):
        return "中年"
    if _contains_any(source, ("老人", "老年", "年迈", "六十", "70", "七十")):
        return "老年"
    return ""


def character_body_hint(character: Character | None) -> str:
    source = _parts(character.appearance if character else None, character.description if character else None)
    if _contains_any(source, ("瘦小", "消瘦", "单薄")):
        return "瘦小单薄的身材"
    if _contains_any(source, ("魁梧", "壮硕", "强壮", "虎背熊腰")):
        return "魁梧强壮的身材"
    if _contains_any(source, ("高挑", "修长")):
        return "高挑修长的身材比例"
    return "自然真实的人体比例"


def xigua_character_sheet_prompt(
    character: Character | None,
    base_prompt: str | None,
    style: ArtStyle | None,
    drama: Drama | None,
    *,
    view_type: str,
    extra: str | None,
) -> str:
    """西瓜短剧人物设定图结构：先锁角色设定图版式，再分层写身份/脸/发型/服装/质感。"""
    identity = _parts(
        character_gender_hint(character),
        character_age_hint(character),
        character_body_hint(character),
        character_identity_guard(character),
    )
    source_description = _parts(
        character.name if character else None,
        character.role if character else None,
        character.appearance if character else None,
        character.personality if character else None,
        character.description if character else None,
        base_prompt,
    )
    view_hint = VIEW_TYPE_PROMPTS.get(view_type, VIEW_TYPE_PROMPTS["turnaround"])
    constraint = style.constraint_manual if (style and style.constraint_manual) else None
    period = character_period_prompt(character, drama)
    return _parts(
        style.prompt_suffix if style else None,
        XIGUA_CHARACTER_SHEET_BASE,
        view_hint,
        "角色身份锁定：画面中只允许出现这个命名角色；所有视图必须保持同一张脸、同一年龄、同一发型、同一服装、同一身材比例",
        f"身份信息：{identity}" if identity else None,
        f"角色原始描述：{source_description}" if source_description else None,
        "脸部设计：自然真实的脸，清晰的面部骨相，真实眼睛，自然鼻子和嘴，真实肤色，轻微皮肤纹理，不要塑料皮肤",
        "发型设计：发型必须符合角色描述，轮廓清晰可辨，所有视图中的发色、发长、发型保持一致",
        "服装设计：完整服装必须符合角色描述，领口、袖子、腰部、裤子/裙子/长袍、鞋子都要清楚可见，布料材质和褶皱明确",
        "姿态和版式：中性站姿，双臂自然放松，不要夸张动作，除非角色描述明确需要否则不要道具，画面内不要文字标签",
        period,
        constraint,
        PROTECTION_PROMPT,
        extra,
    )


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
        "空场景环境图，只画建筑、空间、陈设、风景和光线，不要人物、不要士兵、不要人体轮廓",
        PROTECTION_PROMPT,
    )


def storyboard_composition_prompt(
    storyboard: Storyboard,
    character_refs: list[tuple[Character, str]],
    *,
    scene_reference_index: int | None,
    first_character_reference: int | None = None,
    all_characters: list[Character] | None = None,
) -> str | None:
    """把参考图身份绑定到稳定画面位置和合理景深。"""
    cast = all_characters if all_characters is not None else [row for row, _ in character_refs]
    if not cast:
        return None

    names = [character.name for character in cast]
    count = len(names)
    if count == 1:
        positions = ["画面中央前景"]
        framing = "单人中景"
    elif count == 2:
        positions = ["左侧前景", "右侧前景"]
        framing = (
            "严格双人平视中景，两名具名角色都必须腰部以上清楚可见，人物比例和焦平面一致；"
            "即使基础描述写了特写，也不要变成单人特写"
        )
    elif count == 3:
        positions = ["左侧", "中央", "右侧"]
        framing = "平衡的平视三人构图，三名角色比例一致、焦平面一致"
    else:
        positions = [f"主要角色位置{index + 1}" for index in range(count)]
        framing = "平衡群像构图，所有主要角色比例接近、焦平面一致"

    first_character_reference = first_character_reference or ((scene_reference_index or 0) + 1)
    bindings = []
    positions_by_id = {character.id: positions[index] for index, character in enumerate(cast)}
    for index, (character, _) in enumerate(character_refs):
        identity = _parts(character_identity_guard(character), character.appearance)
        bindings.append(
            f"参考图{first_character_reference + index}是{character.name}，只出现一次，位置在{positions_by_id.get(character.id, '符合剧情的自然位置')}；"
            f"复制参考图里的准确脸型、年龄、发型、胡须和服装"
            + (f"；身份细节：{identity}" if identity else "")
        )
    cast_definitions = "; ".join(
        f"{character.name}: {_parts(character_identity_guard(character), character.appearance) or '保持已建立的角色身份'}"
        for character in cast
    )
    crowd_terms = ("众人", "士兵们", "人群", "军队", "百姓", "crowd", "army", "soldiers", "troops")
    source = _parts(storyboard.action, storyboard.image_prompt)
    extras = (
        "如果剧情需要背景群众，只能是远处、小尺寸、自然分散的剪影；不要排成一排，不要抢主要角色画面"
        if any(term.lower() in source.lower() for term in crowd_terms)
        else "不要添加额外前景或中景人物；不要人群；不要人物排队"
    )
    scene_rule = (
        f"参考图{scene_reference_index}只控制建筑、光线和环境；忽略并移除该参考图里所有人物或士兵"
        if scene_reference_index is not None
        else None
    )
    return _parts(
        f"构图锁定：画面中必须正好出现{count}名具名主要角色：{', '.join(names)}",
        framing,
        f"角色定义：{cast_definitions}",
        "; ".join(bindings) if bindings else None,
        scene_rule,
        extras,
        "让这些具名角色共同完成当前镜头描述的动作；保持自然透视和真实人体比例",
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
        return db.scalars(select(ArtStyle).order_by(ArtStyle.sort_order, ArtStyle.id)).first()
    style = db.get(ArtStyle, art_style_id)
    if style is None:
        raise LookupError("画风不存在")
    return style


def character_period_prompt(character: Character | None, drama: Drama | None) -> str | None:
    """从项目和人物设定提取时代硬约束，避免“士兵”被画成现代制服。"""
    source = _parts(
        drama.title if drama else None,
        drama.description if drama else None,
        drama.style if drama else None,
        character.appearance if character else None,
        character.image_prompt if character else None,
    )
    tang_markers = ("唐代", "唐朝", "唐军", "长安", "香积寺", "郭子仪", "李俶")
    if any(marker in source for marker in tang_markers):
        return (
            "八世纪唐代历史战争短剧，唐代准确服饰，真实旧化扎甲或唐代铠甲，内穿交领汉服/圆领袍，古代中国军旅造型，"
            "严格禁止现代军装、现代衬衫裤子、拉链、现代口袋、现代徽章、肩章、迷彩、枪械"
        )
    ancient_markers = ("古代", "王朝", "战甲", "铠甲", "长袍", "将军", "皇子")
    if any(marker in source for marker in ancient_markers):
        return (
            "中国古代历史服化道，传统服饰和铠甲必须符合时代，严格禁止现代服装、拉链、现代军装、枪械"
        )
    return None


def character_identity_guard(character: Character | None) -> str | None:
    """把常见年龄/体型设定补成模型更稳定理解的中文硬约束。"""
    if character is None:
        return None
    source = _parts(character.name, character.appearance, character.image_prompt)
    values: list[str] = []
    if "少年" in source or "少年兵" in source:
        values.append(
            "16到18岁的少年男性，脸部明显年轻，干净无胡须，不要络腮胡，不要中年感"
        )
    elif "青年" in source or "年轻" in source:
        values.append("青年，年轻脸")
    elif "中年" in source:
        values.append("中年成人")
    elif "老人" in source or "老年" in source:
        values.append("老年成人，符合年龄的脸部特征")
    if "瘦小" in source:
        values.append("瘦小身材，窄肩，体型单薄")
    elif "魁梧" in source or "壮硕" in source or "虎背熊腰" in source:
        values.append("魁梧强壮的体格")
    return ", ".join(values) or None


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
    references: list[str],
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
        "reference_images": json.dumps(references, ensure_ascii=False) if references else None,
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
        references=references,
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
    view_type: str = "turnaround",
) -> GenerationOutcome:
    character = db.get(Character, character_id) if character_id is not None else None
    if character_id is not None and (character is None or character.deleted_at is not None):
        raise LookupError("角色不存在")
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    style = _style(db, art_style_id)
    drama = db.get(Drama, character.drama_id) if character is not None else None

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
        base_prompt = build_character_prompt(character, None, None, scene, action, consistent, view_type)
    if character is not None:
        character.image_prompt = base_prompt  # 附加指令仅用于本次生成，不污染可编辑提示词
        character.view_type = view_type  # 记录本次出图视角
    # 人物资产统一走「西瓜短剧角色设定图」结构：
    # 先锁设定图版式/身份一致性，再写脸、发型、服装、身材、材质，避免生成随手拍或风格漂移。
    prompt = xigua_character_sheet_prompt(
        character,
        base_prompt,
        style,
        drama,
        view_type=view_type,
        extra=extra,
    )

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
    episode = db.get(Episode, sb.episode_id)
    characters = sync_storyboard_characters(db, sb)
    scene = match_storyboard_scene(db, sb)
    reference_rows, _reference_mode = resolve_storyboard_image_references(
        db, sb, characters=characters, scene=scene,
    )
    references = [row.url for row in reference_rows]
    workflow = KONTEXT_WORKFLOW if references else FLUX_WORKFLOW

    style = _style(db, art_style_id)
    constraint = style.constraint_manual if (style and style.constraint_manual) else None
    reference_labels: list[str] = []
    character_refs: list[tuple[Character, str]] = []
    first_character_reference: int | None = None
    scene_reference_index: int | None = None
    for reference_index, reference in enumerate(reference_rows, start=1):
        if reference.kind == "scene":
            scene_reference_index = reference_index
            reference_labels.append(
                f"参考图{reference_index}（{reference.label}）只控制建筑、光线和环境；不要复制其中任何人物"
            )
        elif reference.kind in ("continuity", "previous"):
            role = "不可随意改变的基础画面" if reference.kind == "continuity" else "紧邻上一镜头"
            source = db.get(Storyboard, reference.source_storyboard_id) if reference.source_storyboard_id else None
            source_ids = set(db.scalars(select(StoryboardCharacter.character_id).where(
                StoryboardCharacter.storyboard_id == source.id
            )).all()) if source is not None else set()
            matching_names = [character.name for character in characters if character.id in source_ids]
            identity_rule = (
                f"；它同时是{', '.join(matching_names)}在画面中的权威身份参考：必须复制他们准确的脸、年龄、发型、眼镜、服装和身体比例"
                if matching_names else ""
            )
            reference_labels.append(
                f"图片{reference_index}/参考图{reference_index}（{reference.label}）是{role}；保持相同的房间结构、窗户和柜台位置、"
                "光线方向、画面朝向和空间地理关系；在这张图基础上调整，不要重新想象场景；匹配到的角色必须尽量像原图，"
                "不要重新设计他们的脸、眼镜、发型或服装；只按当前镜头调整构图，不要添加当前镜头未命名的人物"
                + identity_rule
            )
        elif reference.kind == "character":
            character = next((row for row in characters if reference.url in (_reference(row),)), None)
            if character is not None:
                if first_character_reference is None:
                    first_character_reference = reference_index
                character_refs.append((character, reference.url))
                identity = _parts(character_identity_guard(character), character.appearance)
                reference_labels.append(
                    f"参考图{reference_index}是角色{character.name}；保持准确脸型、年龄、发型、服装和身份；不要和其他角色混脸"
                    + (f"；准确身份细节：{identity}" if identity else "")
                )
        else:
            reference_labels.append(
                f"参考图{reference_index}（{reference.label}）是用户锁定的视觉参考；只遵循其中相关的身份、环境或构图信息"
            )
    composition = storyboard_composition_prompt(
        sb,
        character_refs,
        scene_reference_index=scene_reference_index,
        first_character_reference=first_character_reference,
        all_characters=characters,
    )
    prompt = _parts(
        style.prompt_suffix if style else None,
        constraint,
        "; ".join(reference_labels) if reference_labels else None,
        composition,
        "以上连续性锁定和角色锁定优先级最高，可覆盖基础描述中冲突的单人构图",
        sb.image_prompt,
        sb.action,
        PROTECTION_PROMPT,
        extra,
    )

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
            negative=STORYBOARD_NEGATIVE_PROMPT,
            width=width,
            height=height,
            seed=seed,
            workflow=workflow,
            reference_images=references,
        )
    )
    if result.status != "completed":
        raise AssetGenerationError(result.error or "分镜出图失败")

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
        negative=STORYBOARD_NEGATIVE_PROMPT,
        node=node,
        result=result,
        resolution=resolution_key,
        width=width,
        height=height,
        seed=seed,
        references=references,
    )
    asset.width = width
    asset.height = height
    asset.image_gen_id = generation.id
    db.commit()
    db.refresh(asset)
    return GenerationOutcome(asset, result, compliance, prompt, workflow)


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
        base_prompt = build_scene_prompt(scene, None, None)
    if scene is not None:
        scene.prompt = base_prompt  # 附加指令仅用于本次生成，不污染可编辑提示词
    constraint = style.constraint_manual if (style and style.constraint_manual) else None
    prompt = _parts(
        style.prompt_suffix if style else None,
        base_prompt,
        "空场景环境图，只画建筑、空间、陈设、风景和光线，不要人物、不要士兵、不要人体轮廓",
        constraint,
        PROTECTION_PROMPT,
        extra,
    )

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
        negative=SCENE_NEGATIVE_PROMPT,
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
    constraint = style.constraint_manual if (style and style.constraint_manual and not (full_prompt and full_prompt.strip())) else None
    prompt = _parts(base_prompt, constraint, extra)

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
