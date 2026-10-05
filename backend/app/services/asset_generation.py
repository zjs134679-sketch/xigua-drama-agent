from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import random
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from pathlib import Path

from app.models.domain import (
    ArtStyle, Asset, Character, Drama, Episode, ImageGeneration, Prop, Scene,
    Storyboard, StoryboardCharacter,
)
from app.services.art_style_pack import style_image_constraint
from app.services.compliance import FilterResult, check
from app.services.compliance import enforce
from app.services.compute import ImageJob, JobResult, get_node
from app.services.storyboard_references import (
    match_storyboard_scene,
    resolve_storyboard_image_references,
    sync_storyboard_characters,
    _asset_url as _storyboard_asset_url,
)
from app.services.style_composer import compose
from app.services.style_contract import STAGE_IDENTITY, STAGE_SCENE_ENV, STAGE_SHOT_IMAGE


def _style_block_for_asset(
    db: Session,
    style: ArtStyle | None,
    *,
    stage: str,
    drama: Drama | None = None,
) -> str | None:
    """分阶段风格段：优先 StyleContract，失败则回退手册摘要。"""
    try:
        contract = compose(db=db, drama=drama, stage=stage, art_style=style)
        block = contract.image_constraint_block()
        if block:
            return block
    except Exception:  # noqa: BLE001
        pass
    return style_image_constraint(style)

# 唯一启用的 H3 Turbo 工作流（旧 Flux/Kontext/LTX/i2v 已弃用）
H3_T2I_WORKFLOW = "minimax-h3-t2i.api.json"
H3_R2V_WORKFLOW = "minimax-h3-r2v.api.json"
# 兼容旧变量名：一律指向 Turbo 文生图
FLUX_WORKFLOW = H3_T2I_WORKFLOW
LEGACY_FLUX_WORKFLOW = H3_T2I_WORKFLOW
KONTEXT_WORKFLOW = H3_T2I_WORKFLOW
# 负向/保护语统一自 negative_packs.yaml（题材可关军事禁令等）
from app.services.negative_packs import (  # noqa: E402
    character_negative as _character_negative,
    drama_genre_context as _drama_genre_context,
    empty_plate_prompt as _empty_plate_prompt,
    general_negative as _general_negative,
    protection_prompt as _protection_prompt,
    scene_empty_lead as _scene_empty_lead,
    scene_empty_tail as _scene_empty_tail,
    scene_negative as _scene_negative,
    storyboard_negative as _storyboard_negative,
)

# 模块级常量：测试与旧代码兼容（默认题材快照）
PROTECTION_PROMPT = _protection_prompt()
EMPTY_PLATE_PROMPT = _empty_plate_prompt()
SCENE_EMPTY_LEAD = _scene_empty_lead()
SCENE_EMPTY_TAIL = _scene_empty_tail()
CHARACTER_PORTRAIT_PROMPT = (
    "单个角色，浅灰色干净影棚背景，背景简洁无杂物，角色居中，适合作为后续分镜参考图"
)
CHARACTER_NEGATIVE_PROMPT = _character_negative()
GENERAL_NEGATIVE_PROMPT = _general_negative()
SCENE_NEGATIVE_PROMPT = _scene_negative()
STORYBOARD_NEGATIVE_PROMPT = _storyboard_negative(empty_plate=False)
STORYBOARD_EMPTY_NEGATIVE = _storyboard_negative(empty_plate=True)


def _genre_for_drama(db: Session, drama_id: int | None) -> tuple[str | None, str | None]:
    return _drama_genre_context(db, drama_id)

# 会诱导模型画字的剧本文案 → 出图前剔除
_ON_SCREEN_TEXT_PATTERNS = (
    r"字幕浮现", r"字幕出现", r"字幕升起", r"字幕落下", r"字幕闪过", r"字幕",
    r"标题卡", r"片头字", r"花字", r"大字报", r"屏幕字", r"艺术字",
    r"水印", r"logo", r"Logo", r"LOGO",
    r"on[- ]?screen text", r"subtitle[s]?", r"caption[s]?", r"title card",
    r"with text", r"text overlay", r"floating text", r"chinese text",
)
# 仅「明确空镜」才无人；禁止用「起幅/落幅/摇镜」单独判断——拆镜后每段动作都带这些词
_ENV_ONLY_HINTS = (
    "空镜", "空景", "无人", "全景空", "纯环境", "环境空镜", "无人街道", "无人空镜",
    "establishing shot", "empty street", "no people", "no person", "empty plate",
)
# 镜头/环境建立类用语（无点名角色时倾向空镜）
_CAMERA_ENV_HINTS = (
    "镜头", "机位", "摇向", "摇至", "推近", "拉远", "全景", "远景", "空镜",
    "城楼", "街道", "坊墙", "城门", "废墟", "纸钱", "夜色", "建立方位", "运镜",
    "残破", "断壁", "营火位置", "建筑",
)
# 拆镜阶段标签：检测空镜前先剥掉，避免误判
_PHASE_TAG_RE = re.compile(
    r"【(?:起幅|中段\d*|落幅)】|[·・](?:起幅|中段\d*|落幅)|(?:^|[\s，,])(?:起幅|中段\d*|落幅)(?=[\s，,]|$)"
)
# 旁白/画外音：有字但不等于画面有人
_VOICEOVER_RE = re.compile(
    r"^(旁白|画外音|解说|VO|OS|NARRATOR|Narrator)\s*[：:：]",
    re.IGNORECASE,
)
_PEOPLE_ACTION_HINTS = (
    "磨刀", "说话", "对话", "拍肩", "握刀", "持刀", "士兵", "将军", "人物", "角色",
    "脸", "眼神", "低头", "抬头", "手抖", "拥抱", "厮杀", "奔跑",
    "跪地", "骑马", "下马", "进城", "回营", "擦刀", "对视",
)
RESOLUTION_PRESETS: dict[str, tuple[int, int]] = {
    # 快速档（省算力，预览用）
    "fast_portrait_640x896": (640, 896),
    "fast_landscape_864x480": (864, 480),
    "portrait_768x1024": (768, 1024),
    "square_1024x1024": (1024, 1024),
    "landscape_1024x576": (1024, 576),
    # 场景超清横屏（底板参考图，与成片视频分辨率独立）
    "uhd_landscape_1280x720": (1280, 720),
    "uhd_landscape_1536x864": (1536, 864),
    # 高清竖屏：角色定装默认（脸部细节更清晰）
    "hd_portrait_896x1152": (896, 1152),
    # 超清竖屏：脸部锁定 / 定装精修
    "uhd_portrait_1024x1344": (1024, 1344),
    # 更高竖屏（更吃显存，脸部更清晰）
    "uhd_portrait_1152x1536": (1152, 1536),
}
DEFAULT_RESOLUTION = {
    # 角色默认超清竖屏（定妆参考图，与视频分辨率独立）
    "character": "uhd_portrait_1024x1344",
    # 场景默认超清横屏（与角色同级可选清晰度）
    "scene": "uhd_landscape_1280x720",
    # 道具默认方形，便于单物件特写
    "prop": "square_1024x1024",
    "storyboard": "fast_landscape_864x480",
}

# 采样步数：H3 Turbo 4 步 LoRA 工作流下默认 4（提再高会被 DualClock+Turbo 钳回或报错）
DEFAULT_STEPS = {
    "character": 4,
    "scene": 4,
    "prop": 4,
    "storyboard": 4,
}

# 角色、场景、道具步数允许用户覆盖（4～50；有 Turbo LoRA 时运行时强制 4）
CHARACTER_STEPS_MIN = 4
CHARACTER_STEPS_MAX = 50
SCENE_STEPS_MIN = 4
SCENE_STEPS_MAX = 50
PROP_STEPS_MIN = 4
PROP_STEPS_MAX = 50
# 角色定装额外画质锚词（拼进正向）
CHARACTER_SHARPNESS_PROMPT = (
    "超清人脸，五官锐利清晰，瞳孔高光，皮肤纹理自然细腻，发丝根根分明，"
    "无糊脸，无涂抹感，无过度磨皮，85mm 肖像镜头，浅景深背景虚化，"
    "电影级棚拍柔光，8k 细节，高分辨率，锐利对焦在眼睛"
)

# 道具定妆：与人物定装同级「干净影棚」；用户提示词里的桌面/战场/房间等会诱导出场景，必须清洗
XIGUA_PROP_SHEET_BASE = (
    "西瓜短剧写实道具设定图，专业道具参考照，短剧写实摄影风格，"
    "不要动漫风，不要卡通风，不要游戏渲染，"
    "超高细节，材质纹理清晰，边缘锐利，干净影棚光"
)
# 强制影棚底板（放在提示词最前与最后各钉一次）
PROP_STUDIO_BACKGROUND_PROMPT = (
    "浅灰干净影棚背景，背景简洁无杂物，浅灰色纯净背景，"
    "干净影棚光，均匀柔光，无桌面无地面接缝无墙面场景，"
    "无风景无房间无人物无手，单个道具完整居中，适合作为后续分镜道具参考图"
)
PROP_HARD_STUDIO_LOCK = (
    "【强制底板·最高优先级】背景必须是纯净影棚浅灰或纯白无缝背景（#FFFFFF 或浅灰），"
    "只画道具本体的外形、颜色、材质、磨损与反光；"
    "禁止木桌、柜台、台面、地面、墙面、房间、大帐、战场、街道、风景、黄昏、窗光环境；"
    "禁止人物、手、握持、跪姿、士兵；禁止背景虚化成场景；禁止任何环境叙事"
)
PROP_STUDIO_BACKGROUND_NEGATIVE = (
    "复杂背景, 场景, 房间, 室外, 风景, 战场, 军营, 大帐, 街道, 地面纹理, 木桌, 石台, 柜台, "
    "墙面, 货架, 窗, 烛台, 黄昏, 晨曦环境, 渐变背景, 彩色背景, 暗背景, 黑背景, "
    "人物, 手, 人群, 手指, 握持, 士兵, 跪地, 持械 demonstrator, "
    "cluttered background, environment, landscape, battlefield, floor, wall, table, desk, "
    "busy backdrop, bokeh environment, scenic background, text, watermark, logo, subtitle"
)

# 用户/润色提示词中会「带出背景」的诱导片段（整句删除）
_PROP_ENV_KILL_TERMS = (
    "背景", "虚化", "木桌", "桌面", "柜台", "台面", "战场", "大帐", "军营", "房间",
    "室外", "风景", "街道", "地面", "墙面", "墙", "侧窗", "窗光", "窗", "烛光",
    "黄昏", "晨光", "逆光", "景深", "氛围", "环境", "货架", "士兵", "人物",
    "紧握", "握着", "跪", "手持", "双手", "悬浮在空中", "铺在", "置于", "静置于",
    "斜照", "投下", "光影勾勒", "怀旧", "战场黄昏", "自然光", "侧光斜照",
    "静物构图", "电影感", "统一风格", "摄影质感", "清晰对焦", "高细节对焦",
    "暖黄", "暗调", "侧光", "顶光", "脚灯", "舞台", "剧照", "空间",
)
# 用户可见提示词末尾固定影棚句（润色/落库后都带上，避免再被写成场景）
PROP_USER_STUDIO_TAIL = "浅灰干净影棚背景，背景简洁无杂物，单件居中，无桌面无场景无人物"


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


def strip_on_screen_text_terms(text: str | None) -> str:
    """去掉会诱导模型画字/字幕的词，保留其余视觉描述。"""
    value = (text or "").strip()
    if not value:
        return ""
    for pattern in _ON_SCREEN_TEXT_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[，,]{2,}", "，", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" ，,;；")


def _strip_phase_tags(text: str | None) -> str:
    """去掉【起幅】/·落幅 等拆镜标签，再判断是否空镜。"""
    return _PHASE_TAG_RE.sub(" ", text or "").strip()


def is_voiceover_only_dialogue(dialogue: str | None) -> bool:
    """旁白/画外音/解说/无说话人叙述：画面不必出现说话人。"""
    text = (dialogue or "").strip()
    if not text:
        return True
    if _VOICEOVER_RE.match(text):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(_VOICEOVER_RE.match(ln) or ln.startswith("旁白") for ln in lines):
        return True
    if "旁白" in text or "画外" in text:
        return True
    # 无「角色名：」对白形态 → 当作旁白/解说（如「唐军败过…他们又回来了。」）
    if not has_character_speech_lines(text):
        return True
    return False


def has_character_speech_lines(dialogue: str | None) -> bool:
    """是否含真人对白行（角色名：台词），排除旁白："""
    text = (dialogue or "").strip()
    if not text:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _VOICEOVER_RE.match(line) or line.startswith("旁白"):
            continue
        # 阿石：… / 陈七：…
        if re.match(r"^[\u4e00-\u9fffA-Za-z0-9_·]{1,12}\s*[:：]", line):
            return True
    return False


def is_environment_only_shot(storyboard: Storyboard, characters: list[Character]) -> bool:
    """
    空环境镜判定：
    - 真人对白（角色名：）/ 点名角色 / 人物动作 → 有人镜
    - 仅旁白或无说话人叙述 + 镜头扫环境（长安夜色 #01–#05）→ 空镜
    - 明确「空镜/无人」→ 空镜
    """
    dialogue = (dialogue_raw := (storyboard.dialogue or "").strip())
    # 仅「角色名：台词」才强制有人；旁白/解说/无前缀叙述都不算
    if has_character_speech_lines(dialogue):
        return False

    # 人物线索只看标题/动作/描述，不看 image_prompt
    # （提示词常写「不要人物/士兵」，会误命中人物词）
    story_text = _parts(storyboard.title, storyboard.action, storyboard.description)
    prompt_text = storyboard.image_prompt or ""
    raw = _parts(story_text, prompt_text)
    story_blob = _strip_phase_tags(story_text) or story_text or ""
    full_blob = _strip_phase_tags(raw) or raw or ""

    # 点名具体角色（在动作/标题里）→ 有人
    for character in characters:
        name = (character.name or "").strip()
        if name and len(name) >= 2 and name in (story_text or ""):
            return False

    # 去掉「不要/禁止/勿 + 人物词」后再查人物动作，避免「不要士兵」误判
    story_for_people = re.sub(
        r"(不要|禁止|勿|别|无|没有|不含)[^，。；;\n]{0,12}(人物|士兵|将军|行人|路人|百姓|人群)",
        " ",
        story_blob,
    )
    if any(hint in story_for_people for hint in _PEOPLE_ACTION_HINTS):
        return False

    # 明确空镜（标题/动作/提示词均可）
    if any(hint in full_blob for hint in _ENV_ONLY_HINTS):
        return True

    # 镜头/环境建立用语 + 无点名角色 → 空镜
    # 覆盖：#05「摇镜落幅…纸钱」+ 叙述旁白「唐军败过…他们又回来了」
    camera_env = any(hint in full_blob for hint in _CAMERA_ENV_HINTS) or any(
        hint in story_blob for hint in ("摇镜", "落幅", "起幅", "纸钱", "火光", "城楼", "街道")
    )
    named_in_text = any(
        (c.name or "") in (story_text or "") for c in characters if c.name and len(c.name) >= 2
    )
    if camera_env and not named_in_text:
        return True

    # 标题含「夜色/空镜」且动作无人物
    title = (storyboard.title or "")
    if any(k in title for k in ("夜色", "空镜", "空景", "建立")) and not named_in_text:
        return True

    # 有关联角色但文案无人物线索、无镜头空镜特征：仍按有人
    if characters and not camera_env:
        return False

    # 无角色、无真人对白、无人物动作 → 空镜
    return True


def scrub_people_from_prompt(text: str | None) -> str:
    """空镜/场景出图前，去掉会诱导出人的词（身份/动作/姓名结构等）。"""
    value = text or ""
    patterns = (
        r"全身与环境关系清楚",
        r"全身",
        r"人物位于画面",
        r"人物[：:].{0,40}",
        r"角色[：:].{0,40}",
        r"配角|主角|男主|女主|龙套",
        r"士兵|将军|武将|军官|侍卫|卫兵|行人|路人|百姓|人群|人影|剪影人|有人|两人|三人|一名|一位|几个",
        r"男人|女人|小孩|儿童|老人|少年|青年|中年|老兵|新兵",
        # 动作与表情（场景资产不应保留）
        r"站在|坐在|跪在|躺在|站着|坐着|跪着|躺着|奔跑|对话|说话|对视|讨论|交谈|磨刀|握刀|持刀|骑马|下马",
        r"面容|神色|表情|目光|眼神|低头|抬头|手抖|拥抱|厮杀|凝重|肃穆|颤抖",
        # 「某某与某某」/ 残留「某某与」
        r"[\u4e00-\u9fff]{2,4}[与和][\u4e00-\u9fff]{2,4}",
        r"[\u4e00-\u9fff]{2,4}[与和]\s*",
        r"\b(person|people|human|soldier|warrior|man|woman|child|crowd|figure|portrait|character|silhouette)\b",
        r"two soldiers|a man|a woman|old man|young man",
    )
    for pattern in patterns:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"[，,]{2,}", "，", value)
    return value.strip(" ，,;；")


def _filter_env_style_block(block: str | None) -> str | None:
    """场景造景：去掉风格段里会诱导画人的条款（定装/五官/角色等）。"""
    if not block or not block.strip():
        return None
    parts = re.split(r"[。；;\n]", block)
    kept: list[str] = []
    ban = re.compile(r"角色|人物|五官|服装|定装|站姿|身份|人脸|表情|发型|身材|选角")
    for part in parts:
        p = part.strip()
        if not p or ban.search(p):
            continue
        kept.append(p)
    text = "。".join(kept)
    return text.strip("。") or None


def assemble_scene_prompt(
    *,
    location: str | None,
    user_prompt: str | None,
    style: ArtStyle | None,
    style_block: str | None,
    extra: str | None = None,
) -> str:
    """场景造景统一拼装：前置 empty plate + 清洗人物词 + 尾部硬约束。

    参考短剧/分镜流水线 empty establishing plate 做法：正向前置无人、负向双语扩写、
    用户描述只保留环境信息。
    """
    cleaned = scrub_people_from_prompt(user_prompt)
    cleaned = strip_on_screen_text_terms(cleaned) or cleaned
    env_style = _filter_env_style_block(style_block)
    return _parts(
        _scene_empty_lead() or SCENE_EMPTY_LEAD,
        style.prompt_suffix if style else None,
        location,
        cleaned,
        env_style,
        _scene_empty_tail() or SCENE_EMPTY_TAIL,
        _protection_prompt() or PROTECTION_PROMPT,
        scrub_people_from_prompt(extra) if extra else None,
    )


def segment_environment_lock_prompt(storyboard: Storyboard, *, has_env_reference: bool) -> str | None:
    """同一运镜段落：强制场景/光影/布景一致，保证拆镜后环境不跳戏。"""
    if not storyboard.segment_key or not storyboard.segment_total or storyboard.segment_total < 2:
        return None
    loc = (storyboard.location or "").strip() or "同一场景"
    part = storyboard.segment_part or 1
    total = storyboard.segment_total
    base = (
        f"运镜段落锁定（{part}/{total}）：必须保持与本段落完全相同的环境——"
        f"{loc} 的空间布局、帐篷/建筑位置、地面材质、营火/火光位置、主光方向、色温与氛围；"
        "禁止换成另一个营地、另一条街道或另一套布景；只改变景别/人物动作/机位，不改变场景本身"
    )
    if has_env_reference:
        return _parts(
            base,
            "环境以段落场景锚点/上一镜参考图为准，建筑与道具位置对齐参考，不要重新想象场景",
        )
    return base


def resolve_resolution(resolution: str | None, category: str) -> tuple[str, int, int]:
    key = resolution if resolution in RESOLUTION_PRESETS else DEFAULT_RESOLUTION[category]
    width, height = RESOLUTION_PRESETS[key]
    return key, width, height


VIEW_TYPE_PROMPTS = {
    # 三视图：正面 + 纯侧面 + 背面（3 个全身）
    "turnaround": (
        "角色三视图设定图，同一张图从左到右恰好 3 个全身人物：正面站立、纯左侧身、背面站立；"
        "禁止第四个全身、禁止三分之四侧面、禁止只出一张单人；"
        "三个视图必须是同一角色、同一张脸、同一年龄、同一发型、同一身材、同一套服装；"
        "中性站姿双臂自然下垂，全身从头到脚完整入镜，三人等大横排，浅灰干净影棚背景，高清晰度"
    ),
    # 三视图 + 头部特写（推荐定装）
    "turnaround_head": (
        "角色定装设定拼图：同一张图必须同时包含 4 个区域——"
        "左侧起 3 个等大全身（正面、纯侧面、背面），另有 1 个放大的头部特写（肩部以上脸部大特写）；"
        "四个区域都是同一角色：同一张脸、同一年龄、同一发型、同一服装颜色与纹样；"
        "头部特写要求：脸部占特写框大部分画面，五官锐利清晰，眼睛有神，皮肤纹理自然，发型发丝细节清楚，"
        "正面或微侧均可，适合做人脸锁定参考；全身三视图从头到脚完整；浅灰影棚背景；禁止文字序号水印"
    ),
    "full_body": (
        "全身正面单人设定照，从头到脚完整可见，清楚展示完整服装、身材比例、鞋子与站姿；"
        "脸部也要清晰可辨，不要过小；浅灰影棚背景，高清晰度"
    ),
    "headshot": (
        "头部特写定装照，肩部以上，脸部占画面 60% 以上，五官极清晰，瞳孔眼神清楚，"
        "发型轮廓与发丝细节清楚，肤色与皮肤纹理自然，适合做人脸一致性参考；"
        "浅灰影棚背景，电影感棚拍光，禁止全身、禁止多人物、禁止文字水印"
    ),
    "side": (
        "纯侧面全身设定照，清楚展示侧脸轮廓、鼻梁、发型轮廓和身体侧面比例；"
        "全身从头到脚完整，浅灰影棚背景，高清晰度"
    ),
}


XIGUA_CHARACTER_SHEET_BASE = (
    "西瓜短剧写实人物设定图，专业真人选角参考照，短剧写实摄影风格，电影感真实质感，"
    "不要动漫风，不要卡通风，不要游戏渲染，自然人体比例，自然表情，可信的人体结构，"
    "超高细节，眼睛锐利对焦，发丝清晰，皮肤纹理真实，干净影棚光，浅灰色纯净背景"
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
    vt = view_type if view_type in VIEW_TYPE_PROMPTS else "turnaround_head"
    view_hint = VIEW_TYPE_PROMPTS[vt]
    # 定装：由调用方传入 constraint；此处保留兼容，默认手册摘要
    constraint = style_image_constraint(style)
    period = character_period_prompt(character, drama)

    multi_view = vt in ("turnaround", "turnaround_head")
    # 头部特写 / 定装拼图：再叠一层清晰度要求
    face_boost = CHARACTER_SHARPNESS_PROMPT if vt in ("headshot", "turnaround_head", "turnaround") else (
        "五官清晰，锐利对焦，高细节皮肤与发丝"
    )
    identity_lock = (
        "角色身份锁定：画面中只允许出现这个命名角色；"
        + (
            "各视图必须保持同一张脸、同一年龄、同一发型、同一服装、同一身材比例"
            if multi_view
            else "五官身份必须与角色描述严格一致，可做后续分镜人脸参考"
        )
    )
    face_design = (
        "脸部设计：自然真实的脸，清晰的面部骨相，真实眼睛有神，自然鼻子和嘴，真实肤色，"
        "轻微皮肤纹理，毛孔级细节，不要塑料皮肤、不要磨皮过度"
    )
    if vt in ("headshot", "turnaround_head"):
        face_design += "；头部特写区域必须锐利对焦、五官最大最清楚"

    layout = {
        "turnaround": "姿态版式：3 个全身横排等大，中性站姿，双臂自然放松；不要道具除非描述需要；无文字标签",
        "turnaround_head": (
            "姿态版式：3 个全身横排 + 1 个头部特写（可放在画面右侧或上方独立框）；"
            "全身中性站姿；头部特写脸部占满特写区；无文字标签序号"
        ),
        "full_body": "姿态版式：单人全身居中，中性站姿，双臂自然放松；无文字标签",
        "headshot": "姿态版式：单人头部特写居中，肩部以上，直视镜头或微侧；无文字标签",
        "side": "姿态版式：单人纯侧面全身居中，中性站姿；无文字标签",
    }.get(vt, "姿态版式：中性站姿；无文字标签")

    hair_design = "发型设计：发型必须符合角色描述，轮廓清晰可辨"
    if multi_view:
        hair_design += "，各视图发色、发长、发型完全一致"
    if vt in ("headshot", "turnaround_head"):
        hair_design += "，特写中可见发丝细节"

    clothing = "服装设计：完整服装必须符合角色描述"
    if vt != "headshot":
        clothing += "，领口、袖子、腰部、下装、鞋子清楚可见，布料材质与褶皱明确"
    else:
        clothing += "，领口与肩部服饰在特写中仍可辨认"

    return _parts(
        style.prompt_suffix if style else None,
        XIGUA_CHARACTER_SHEET_BASE,
        view_hint,
        identity_lock,
        f"身份信息：{identity}" if identity else None,
        f"角色原始描述：{source_description}" if source_description else None,
        face_design,
        face_boost,
        hair_design,
        clothing,
        layout,
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
    view_type: str = "turnaround_head",
) -> str:
    identity = None
    if character is not None:
        identity = _parts(character.name, character.appearance)
        if consistent:
            identity = _parts(identity, "外观与参考图保持一致")
    scene_prompt = None
    if scene is not None:
        scene_prompt = _parts(scene.location, scene.prompt)
    view_hint = VIEW_TYPE_PROMPTS.get(view_type, VIEW_TYPE_PROMPTS["turnaround_head"])
    return _parts(
        style.prompt_suffix if style else None,
        identity,
        view_hint,
        custom_prompt,
        scene_prompt,
        action,
        PROTECTION_PROMPT,
    )


def build_scene_prompt(scene: Scene | None, custom_prompt: str | None, style: ArtStyle | None) -> str:
    raw = _parts(
        scene.prompt if scene else None,
        custom_prompt,
        scene.time if scene else None,
    )
    return assemble_scene_prompt(
        location=scene.location if scene else None,
        user_prompt=raw,
        style=style,
        style_block=None,
        extra=None,
    )


def select_on_screen_cast(
    storyboard: Storyboard,
    linked: list[Character],
    character_refs: list[tuple[Character, str]] | None = None,
) -> list[Character]:
    """决定本镜「必须上镜」的角色：跟剧情走，不要把关联表全员塞进画面。

    旧逻辑用全部 linked 人数强制「左中右三人构图」→ 易出定装式并排站立。
    """
    if not linked:
        return []
    story = _parts(storyboard.title, storyboard.action, storyboard.dialogue, storyboard.description)
    # 1) 文案点名优先
    named = [c for c in linked if c.name and len(c.name) >= 2 and c.name in (story or "")]
    if named:
        # 台词说话人置前
        speaker = None
        dialogue = (storyboard.dialogue or "").strip()
        if dialogue:
            m = re.match(r"^\s*([^\s:：]{1,12})\s*[:：]", dialogue.splitlines()[0].strip())
            if m:
                sp = m.group(1).strip()
                for c in named:
                    if c.name == sp or sp in (c.name or "") or (c.name and c.name in sp):
                        speaker = c
                        break
        if speaker is not None:
            rest = [c for c in named if c.id != speaker.id]
            return [speaker, *rest]
        return named

    # 2) 绑定了说话角色
    if storyboard.speaking_character_id:
        hit = next((c for c in linked if c.id == storyboard.speaking_character_id), None)
        if hit is not None:
            return [hit]

    # 3) 仅有角色参考图时，最多取 2 人（避免 3 参考图 → 三人横排）
    ref_chars = [c for c, _ in (character_refs or [])]
    if ref_chars:
        return ref_chars[:2]

    # 4) 全景/远景建立镜：关联角色不当群像
    shot = (storyboard.shot_type or "") + (storyboard.title or "")
    if any(k in shot for k in ("全景", "远景", "大全景", "空镜", "夜色", "建立")):
        return linked[:1]

    # 5) 默认最多 2 名（双人戏），禁止默认 3 人等大构图
    return linked[:2]


def storyboard_composition_prompt(
    storyboard: Storyboard,
    character_refs: list[tuple[Character, str]],
    *,
    scene_reference_index: int | None,
    first_character_reference: int | None = None,
    all_characters: list[Character] | None = None,
) -> str | None:
    """把参考图身份绑定到剧情构图；禁止设定图式并排站立。"""
    linked = all_characters if all_characters is not None else [row for row, _ in character_refs]
    cast = select_on_screen_cast(storyboard, linked, character_refs)
    if not cast:
        return None

    names = [character.name for character in cast if character.name]
    count = len(cast)
    shot = (storyboard.shot_type or "").strip() or "中景"
    action = strip_on_screen_text_terms(storyboard.action) or "完成当前镜头动作"

    if count == 1:
        framing = (
            f"电影分镜构图，景别偏{shot}，以{names[0]}为画面主体；"
            "按动作自然站位或运动，不要影棚定装站姿，不要三视图设定图"
        )
        layout = f"{names[0]}按剧情动作处于合理位置（可为中景/近景），完成：{action}"
    elif count == 2:
        framing = (
            f"电影分镜构图，景别偏{shot}，双人戏；"
            "两人有主次与前后景深，可一前一后或对话机位，禁止等大并排面向镜头定装站立"
        )
        layout = (
            f"主要角色 {names[0]} 与 {names[1]} 按动作互动："
            f"{action}；谁说话/做动作谁更靠前或更清晰，另一人可略侧/略后"
        )
    else:
        # 3+ 人：禁止左中右等大横排
        framing = (
            f"电影分镜构图，景别偏{shot}，多人戏；"
            "主次分明，有前后景与遮挡，禁止三人等大横排站立、禁止角色设定图/三视图版式"
        )
        primary = names[0]
        others = "、".join(names[1:])
        layout = (
            f"以{primary}为主（可完成关键动作或台词），{others}为次要，"
            f"分布在景深不同位置；动作：{action}；不要所有人肩并肩正面站齐"
        )

    first_character_reference = first_character_reference or ((scene_reference_index or 0) + 1)
    cast_ids = {c.id for c in cast}
    bindings = []
    for index, (character, _) in enumerate(character_refs):
        if character.id not in cast_ids:
            # 参考图有但本镜不必上镜：只借身份，不强制入画
            bindings.append(
                f"参考图{first_character_reference + index}是{character.name}的脸与服装身份库；"
                "本镜可不出现该角色；若出现则只复制脸与服装，禁止把设定图多姿态复制进画面"
            )
            continue
        identity = _parts(character_identity_guard(character), character.appearance)
        bindings.append(
            f"参考图{first_character_reference + index}是{character.name}的身份参考（脸、发型、服装）；"
            "若参考图是三视图/多姿态设定图，只取其中一个全身人物的脸与服装，"
            "禁止把图中多个站姿人物一并复制成并排站立；"
            f"本镜中{character.name}只出现一次，按剧情动作摆位，不要证件照式正面站立"
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
        else "不要添加额外前景或中景无名人物；不要人群；不要人物排队合影"
    )
    scene_rule = (
        f"参考图{scene_reference_index}只控制建筑、光线和环境；忽略并移除该参考图里所有人物或士兵"
        if scene_reference_index is not None
        else None
    )
    anti_sheet = (
        "严禁输出角色设定图、三视图、四视图、影棚白底群像、多人等大横排定装；"
        "必须是有景深、有环境、有剧情动作的电影镜头画面"
    )
    return _parts(
        f"构图锁定：本镜主要上镜角色为 {', '.join(names)}（共{count}人，按剧情需要，非定装合影）",
        framing,
        layout,
        f"角色定义：{cast_definitions}",
        "; ".join(bindings) if bindings else None,
        scene_rule,
        extras,
        anti_sheet,
        "保持自然透视和真实人体比例；动作优先于摆拍",
    )


def scrub_prop_environment_from_prompt(text: str | None) -> str:
    """去掉会诱导模型画场景/桌面/人物握持的句子，只保留物件本体描述。

    库内旧提示词与 AI 润色结果常含「木桌、战场、背景虚化、电影感光线」等，与影棚定装冲突。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    # 先剥常见整段模式
    raw = re.sub(r"背景[^，。,；;\n]{0,40}", " ", raw)
    raw = re.sub(r"静*置[于在][^，。,；;\n]{0,30}", " ", raw)
    raw = re.sub(r"(?:铺在|放在|摆在|放于|摆于)[^，。,；;\n]{0,30}", " ", raw)
    raw = re.sub(r"(?:握着|紧握|手持|托着|拿着)[^，。,；;\n]{0,20}", " ", raw)
    chunks = re.split(r"[，,。；;、\n]+", raw)
    kept: list[str] = []
    object_tokens = (
        "材质", "纹理", "金属", "木质", "布", "刀", "旗", "纸", "铜", "铁",
        "磨损", "缺口", "外壳", "表盘", "信封", "矛", "剑", "盾", "箱", "绳",
        "钟", "表", "柄", "刃", "锈", "漆", "釉", "玉", "骨", "革", "丝",
        "形", "色", "长", "宽", "厚", "圆", "方", "尖",
    )
    for chunk in chunks:
        piece = chunk.strip()
        if not piece:
            continue
        if any(term in piece for term in _PROP_ENV_KILL_TERMS):
            continue
        # 纯氛围/光影句（无物件信息）丢掉
        if any(w in piece for w in ("光线", "构图", "电影感", "摄影质感", "统一风格", "清晰对焦", "高细节", "柔光", "硬光")):
            if not any(w in piece for w in object_tokens):
                continue
        # 仍含明显场景主语
        if re.search(r"(士兵|将军|人|手|战场|大帐|军营|街道|房间|窗外)", piece):
            continue
        kept.append(piece)
    cleaned = "，".join(kept).strip(" ，,")
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    return cleaned.strip(" ，,")


def finalize_prop_user_prompt(text: str | None) -> str:
    """用户可见提示词：清洗环境 + 固定影棚尾句（润色/保存用）。"""
    body = scrub_prop_environment_from_prompt(text)
    if not body:
        return PROP_USER_STUDIO_TAIL
    # 去掉已有重复影棚句再追加，避免叠床架屋
    body = re.sub(
        r"[，,]?\s*(?:浅灰干净影棚背景|纯白背景|浅灰色纯净背景)[^，。]*",
        "",
        body,
    ).strip(" ，,")
    return _parts(body, PROP_USER_STUDIO_TAIL) or PROP_USER_STUDIO_TAIL


def build_prop_prompt(prop: Prop | None, custom_prompt: str | None, style: ArtStyle | None) -> str:
    # 不写风格电影感后缀到可编辑区；本体描述清洗后再拼
    body = scrub_prop_environment_from_prompt(
        _parts(
            prop.name if prop else None,
            prop.type if prop else None,
            prop.description if prop else None,
            custom_prompt,
        )
    )
    return finalize_prop_user_prompt(body or (prop.name if prop else "道具"))


def xigua_prop_sheet_prompt(
    prop: Prop | None,
    base_prompt: str | None,
    style: ArtStyle | None,
    *,
    extra: str | None = None,
) -> str:
    """先锁影棚底板，再只写清洗后的物件本体（禁止场景叙事进入）。"""
    # 去掉用户提示词里自带的影棚尾句，避免重复；只取物件本体
    object_body = scrub_prop_environment_from_prompt(base_prompt)
    object_body = re.sub(
        r"[，,]?\s*(?:浅灰干净影棚背景|纯白背景|浅灰色纯净背景|【强制底板)[^，。]*",
        "",
        object_body or "",
    ).strip(" ，,")
    if not object_body:
        object_body = scrub_prop_environment_from_prompt(
            _parts(prop.name if prop else None, prop.type if prop else None, prop.description if prop else None)
        ) or (prop.name if prop else "道具")
    # 画风后缀极易带「电影光影/空间」→ 道具出图不用
    identity = _parts(
        prop.name if prop else None,
        prop.type if prop else None,
        "单个道具，完整入镜，边缘清晰",
    )
    # 短提示更易被 MiniMax/Flux 遵守底板；环境锁放最前与最后
    return _parts(
        PROP_HARD_STUDIO_LOCK,
        PROP_STUDIO_BACKGROUND_PROMPT,
        XIGUA_PROP_SHEET_BASE,
        "姿态版式：单件道具居中特写，无文字标签、无序号、无水印",
        identity,
        f"主体物件：{object_body}",
        "只画道具本体的形状、颜色、材质、磨损与反光，不要写任何使用场景或环境",
        _protection_prompt() or PROTECTION_PROMPT,
        scrub_prop_environment_from_prompt(extra),
        "pure white or light gray seamless studio background, isolated object, no table no room no people no hands",
        PROP_HARD_STUDIO_LOCK,
    )


def _style(
    db: Session,
    art_style_id: int | None,
    *,
    drama: Drama | None = None,
    drama_id: int | None = None,
) -> ArtStyle | None:
    """解析出图用画风。

    优先级：
    1. 请求显式 art_style_id（临时覆盖）
    2. 项目 style_bible.art_style_id / visual_name
    3. 项目 style 字段按名称匹配
    4. 库内 sort_order 第一项（兜底）
    """
    if art_style_id is not None:
        style = db.get(ArtStyle, art_style_id)
        if style is None:
            raise LookupError("画风不存在")
        return style

    d = drama
    if d is None and drama_id is not None:
        d = db.get(Drama, drama_id)
    if d is not None:
        try:
            from app.services.style_composer import drama_bible

            bible = drama_bible(d)
            sid = bible.get("art_style_id")
            if sid is not None:
                try:
                    row = db.get(ArtStyle, int(sid))
                    if row is not None:
                        return row
                except (TypeError, ValueError):
                    pass
            name = (bible.get("visual_name") or d.style or "").strip()
            if name:
                row = db.scalars(select(ArtStyle).where(ArtStyle.name == name)).first()
                if row is not None:
                    return row
        except Exception:  # noqa: BLE001
            name = (d.style or "").strip()
            if name:
                row = db.scalars(select(ArtStyle).where(ArtStyle.name == name)).first()
                if row is not None:
                    return row

    return db.scalars(select(ArtStyle).order_by(ArtStyle.sort_order, ArtStyle.id)).first()


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
    steps: int | None = None,
) -> GenerationOutcome:
    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    resolution_key, width, height = resolve_resolution(resolution, category)
    seed = random.randint(1, 2**31 - 1)
    node = get_node(db, node_id, capability="image")
    # 角色/场景定装默认更高步数；允许请求覆盖（与视频参数完全独立）
    base_steps = DEFAULT_STEPS.get(category) or 12
    if steps is not None:
        try:
            base_steps = int(steps)
        except (TypeError, ValueError):
            pass
    if category == "character":
        base_steps = max(CHARACTER_STEPS_MIN, min(CHARACTER_STEPS_MAX, base_steps))
    elif category == "scene":
        base_steps = max(SCENE_STEPS_MIN, min(SCENE_STEPS_MAX, base_steps))
    elif category == "prop":
        base_steps = max(PROP_STEPS_MIN, min(PROP_STEPS_MAX, base_steps))
    result = await node.text2image(
        ImageJob(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            steps=base_steps,
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
    steps: int | None = None,
    extra: str | None = None,
    view_type: str = "turnaround_head",
) -> GenerationOutcome:
    character = db.get(Character, character_id) if character_id is not None else None
    if character_id is not None and (character is None or character.deleted_at is not None):
        raise LookupError("角色不存在")
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    drama = db.get(Drama, character.drama_id) if character is not None else (
        db.get(Drama, scene.drama_id) if scene is not None else None
    )
    style = _style(db, art_style_id, drama=drama)

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
    # 定装阶段：中性光/身份锁（与分镜图 stage 分离）
    identity_block = _style_block_for_asset(db, style, stage=STAGE_IDENTITY, drama=drama)
    if identity_block and identity_block not in (prompt or ""):
        prompt = _parts(prompt, identity_block)

    # 出图前强制过 xg_prompt_grid 编排
    from app.services.prompt_orchestrate import orchestrate_image_prompt

    drama_id = character.drama_id if character else (scene.drama_id if scene else None)
    ctx = _parts(
        character.name if character else None,
        character.appearance if character else None,
        character.role if character else None,
    )
    prompt = orchestrate_image_prompt(
        db, kind="character", raw_prompt=prompt, context=ctx
    )
    g, n = _genre_for_drama(db, drama_id)
    negative = _character_negative(genre=g, narrative=n) or CHARACTER_NEGATIVE_PROMPT

    return await _generate(
        db,
        prompt=prompt,
        username=username,
        workflow=workflow,
        references=[ref for ref in references if ref],
        drama_id=drama_id,
        name=character.name if character else "自定义角色素材",
        category="character",
        target=character,
        negative=negative,
        node_id=node_id,
        resolution=resolution,
        steps=steps,
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
        # 用户编辑的提示词先去字幕/花字诱导词，再持久化
        sb.image_prompt = strip_on_screen_text_terms(full_prompt)
    if not (sb.image_prompt and sb.image_prompt.strip()):
        raise AssetGenerationError("该分镜没有画面提示词，请先生成分镜")
    # 库内旧提示词也可能含「字幕浮现」等
    sb.image_prompt = strip_on_screen_text_terms(sb.image_prompt) or sb.image_prompt
    episode = db.get(Episode, sb.episode_id)
    characters = sync_storyboard_characters(db, sb)
    scene = match_storyboard_scene(db, sb)
    empty_plate = is_environment_only_shot(sb, characters)
    # 空镜不把关联角色当必须出场；有人镜（军营磨刀等）保留角色与段落环境
    cast_for_refs = [] if empty_plate else characters
    reference_rows, _reference_mode = resolve_storyboard_image_references(
        db, sb, characters=cast_for_refs, scene=scene,
    )
    if empty_plate:
        # 空镜：去掉角色参考；连续帧只当环境，强制忽略其中人物
        reference_rows = [row for row in reference_rows if row.kind != "character"]
        # 若环境底图本身带人（历史脏图），仍保留作环境但 prompt 会强制去人
    references = [row.url for row in reference_rows]
    # 空镜：清洗用户/拆镜提示词里的「全身/士兵」等诱导词
    if empty_plate and sb.image_prompt:
        sb.image_prompt = scrub_people_from_prompt(sb.image_prompt) or sb.image_prompt
    workflow = KONTEXT_WORKFLOW if references else FLUX_WORKFLOW
    has_env_reference = any(row.kind in ("scene", "continuity", "previous") for row in reference_rows)

    drama = db.get(Drama, episode.drama_id) if episode is not None else None
    style = _style(db, art_style_id, drama=drama)
    constraint = _style_block_for_asset(db, style, stage=STAGE_SHOT_IMAGE, drama=drama)
    reference_labels: list[str] = []
    character_refs: list[tuple[Character, str]] = []
    first_character_reference: int | None = None
    scene_reference_index: int | None = None
    # @角色名 / @说话人 锁定块（写入提示词，与参考图序号绑定）
    at_locks: list[str] = []
    for reference_index, reference in enumerate(reference_rows, start=1):
        pic = f"参考图{reference_index}/@图{reference_index}"
        if reference.kind == "scene":
            scene_reference_index = reference_index
            loc = reference.asset_name or "场景"
            reference_labels.append(
                f"{pic} 是 @场景:{loc} 的环境底板（本软件场景资产）；"
                "只锁定建筑、光线、地面与空间布局；"
                + ("不要复制其中任何人物" if empty_plate else "人物可在此环境中活动，但不要改布景")
            )
            at_locks.append(f"@场景:{loc}=@图{reference_index}")
        elif reference.kind == "prop":
            pname = reference.asset_name or "道具"
            reference_labels.append(
                f"{pic} 是 @道具:{pname} 的定妆资产；只复制该道具外形/材质/颜色，"
                "禁止改造成其他物件；影棚定妆图则只取物件本体、忽略其背景"
            )
            at_locks.append(f"@道具:{pname}=@图{reference_index}")
        elif reference.kind in ("continuity", "previous"):
            if empty_plate:
                reference_labels.append(
                    f"{pic}（{reference.label}）只作环境/构图衔接；"
                    "必须忽略并移除参考图中的所有人物、士兵、行人与剪影，只保留建筑与光影"
                )
            else:
                reference_labels.append(
                    f"{pic}（{reference.label}）仅作动作/运镜衔接，不是角色身份主参考；"
                    "角色脸与服装以 @角色 定妆图为准，不要用上一镜改脸"
                )
        elif reference.kind == "character":
            character = None
            if reference.character_id is not None:
                character = next((row for row in characters if row.id == reference.character_id), None)
            if character is None:
                character = next(
                    (
                        row
                        for row in characters
                        if reference.url in (_reference(row), _storyboard_asset_url(row) or "")
                    ),
                    None,
                )
            if character is None and reference.asset_name:
                character = next((row for row in characters if row.name == reference.asset_name), None)
            if character is not None:
                if first_character_reference is None:
                    first_character_reference = reference_index
                character_refs.append((character, reference.url))
                identity = _parts(character_identity_guard(character), character.appearance)
                speaker_tag = "【本镜说话人·口型/表演优先】" if reference.is_speaker else ""
                at_name = f"@{character.name}"
                reference_labels.append(
                    f"{pic} 是 {at_name} 的身份锁定图（本软件人物资产）{speaker_tag}；"
                    f"画面中 {at_name} 的脸、年龄、发型、服装必须与此图一致，禁止与其他角色混脸；"
                    "若该图是三视图/多人物横排设定图，只取其中一个人物，禁止把并排站姿搬进分镜"
                    + (f"；身份细节：{identity}" if identity else "")
                )
                lock = f"{at_name}=@图{reference_index}"
                if reference.is_speaker:
                    lock = f"@说话人:{character.name}=@图{reference_index}（台词由该角色说出）"
                at_locks.append(lock)
        else:
            reference_labels.append(
                f"{pic}（{reference.label}）是用户锁定的视觉参考；只遵循其中相关的身份、环境或构图信息"
            )
    if at_locks:
        reference_labels.insert(
            0,
            "【资产锁定表】" + "；".join(at_locks) + "。生成时严格按 @名=@图N 对应，禁止串脸/串景/串道具",
        )
    composition = None if empty_plate else storyboard_composition_prompt(
        sb,
        character_refs,
        scene_reference_index=scene_reference_index,
        first_character_reference=first_character_reference,
        all_characters=characters,
    )
    clean_action = strip_on_screen_text_terms(sb.action)
    clean_extra = strip_on_screen_text_terms(extra)
    if empty_plate:
        clean_action = scrub_people_from_prompt(clean_action)
        clean_extra = scrub_people_from_prompt(clean_extra)
    env_lock = segment_environment_lock_prompt(sb, has_env_reference=has_env_reference)
    # 角色设定未写眼镜时，正向+负向同时禁止加镜（模型常给络腮胡中年人乱加眼镜）
    cast_needs_glasses = any(
        any(
            w in ((c.appearance or "") + (c.description or "") + (c.image_prompt or ""))
            for w in ("眼镜", "glasses", "spectacles")
        )
        for c in characters
    )
    no_glasses_pos = None if cast_needs_glasses or empty_plate else "角色不要戴眼镜、墨镜、单片镜，除非角色设定明确要求"
    # 出图前：xg_prompt_grid 编排 image_prompt 主体（保留参考/风格锁定层）
    from app.services.duration_align import clamp_shot_duration
    from app.services.prompt_orchestrate import orchestrate_image_prompt

    core_visual = scrub_people_from_prompt(sb.image_prompt) if empty_plate else (sb.image_prompt or "")
    core_visual = orchestrate_image_prompt(
        db,
        kind="storyboard",
        raw_prompt=core_visual or (clean_action or "分镜画面"),
        context=_parts(sb.title, sb.location, sb.time, sb.shot_type, sb.action),
        duration_sec=clamp_shot_duration(sb.duration),
    )
    prompt = _parts(
        style.prompt_suffix if style else None,
        constraint,
        env_lock,
        "; ".join(reference_labels) if reference_labels else None,
        composition,
        None if empty_plate else "以上环境锁定与角色锁定优先级最高；只改本镜动作/景别，不改场景",
        (_empty_plate_prompt() or EMPTY_PLATE_PROMPT) if empty_plate else None,
        "严格禁止出现任何人物、士兵、行人、面部、肢体、人影剪影" if empty_plate else None,
        core_visual,
        clean_action,
        no_glasses_pos,
        _protection_prompt() or PROTECTION_PROMPT,
        clean_extra,
    )
    g, n = _genre_for_drama(db, episode.drama_id if episode else None)
    negative = _storyboard_negative(
        empty_plate=empty_plate,
        genre=g,
        narrative=n,
        no_glasses=(not cast_needs_glasses and not empty_plate),
    ) or (STORYBOARD_EMPTY_NEGATIVE if empty_plate else STORYBOARD_NEGATIVE_PROMPT)

    compliance = check(prompt)
    if compliance.blocked:
        audit = enforce.record_violation(db, username, compliance, "image_prompt")
        raise ComplianceBlocked(compliance, audit)

    resolution_key, width, height = resolve_resolution(resolution, "storyboard")
    seed = random.randint(1, 2**31 - 1)
    node = get_node(db, node_id, capability="image")
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


async def generate_scene_asset(
    db: Session,
    *,
    scene_id: int | None = None,
    full_prompt: str | None = None,
    art_style_id: int | None = None,
    username: str | None = None,
    node_id: int | None = None,
    resolution: str | None = None,
    steps: int | None = None,
    extra: str | None = None,
) -> GenerationOutcome:
    scene = db.get(Scene, scene_id) if scene_id is not None else None
    if scene_id is not None and (scene is None or scene.deleted_at is not None):
        raise LookupError("场景不存在")
    drama = db.get(Drama, scene.drama_id) if scene is not None else None
    style = _style(db, art_style_id, drama=drama)

    # 可编辑提示词：保留用户原文（仅清洗明显人物诱导），出图时再强制 empty plate 拼装
    if full_prompt and full_prompt.strip():
        editable = scrub_people_from_prompt(full_prompt.strip()) or full_prompt.strip()
    elif scene is not None and scene.prompt and scene.prompt.strip():
        editable = scrub_people_from_prompt(scene.prompt.strip()) or scene.prompt.strip()
    else:
        editable = scrub_people_from_prompt(
            _parts(
                scene.location if scene else None,
                scene.time if scene else None,
            )
        ) or (scene.location if scene else "场景")

    if scene is not None:
        # 落库清洗后的环境描述，避免下次再带出「士兵/人物」
        scene.prompt = editable

    # 关键：用场景环境阶段，禁止 identity 定装条款漏进场景
    style_block = _style_block_for_asset(db, style, stage=STAGE_SCENE_ENV, drama=drama)
    from app.services.prompt_orchestrate import orchestrate_image_prompt

    editable = orchestrate_image_prompt(
        db,
        kind="scene",
        raw_prompt=editable,
        context=_parts(scene.location if scene else None, scene.time if scene else None),
    )
    prompt = assemble_scene_prompt(
        location=scene.location if scene else None,
        user_prompt=editable,
        style=style,
        style_block=style_block,
        extra=extra,
    )
    g, n = _genre_for_drama(db, scene.drama_id if scene else None)
    negative = _scene_negative(genre=g, narrative=n) or SCENE_NEGATIVE_PROMPT

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
        negative=negative,
        node_id=node_id,
        resolution=resolution or DEFAULT_RESOLUTION["scene"],
        steps=steps,
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
    steps: int | None = None,
    extra: str | None = None,
) -> GenerationOutcome:
    prop = db.get(Prop, prop_id) if prop_id is not None else None
    if prop_id is not None and (prop is None or prop.deleted_at is not None):
        raise LookupError("道具不存在")
    drama = db.get(Drama, prop.drama_id) if prop is not None else None
    style = _style(db, art_style_id, drama=drama)

    if full_prompt and full_prompt.strip():
        base_prompt = full_prompt.strip()
    elif prop is not None and prop.prompt and prop.prompt.strip():
        base_prompt = prop.prompt.strip()
    else:
        base_prompt = build_prop_prompt(prop, None, style)
    # 清洗 + 固定影棚尾句；写回库，避免 AI 润色/旧文案下次再带环境
    base_prompt = finalize_prop_user_prompt(base_prompt) or (
        finalize_prop_user_prompt(prop.name if prop else "道具")
    )
    if prop is not None:
        prop.prompt = base_prompt
    # 不用 orchestrate / 画风长段：都会加「光线/空间/电影感」从而画出场景
    prompt = xigua_prop_sheet_prompt(prop, base_prompt, None, extra=extra)

    g, n = _genre_for_drama(db, prop.drama_id if prop else None)
    base_neg = _general_negative(genre=g, narrative=n) or GENERAL_NEGATIVE_PROMPT
    negative = _parts(base_neg, PROP_STUDIO_BACKGROUND_NEGATIVE)

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
        negative=negative,
        node_id=node_id,
        resolution=resolution or DEFAULT_RESOLUTION["prop"],
        steps=steps,
    )
