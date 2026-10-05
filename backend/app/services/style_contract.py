"""风格机读契约 StyleContract —— 西瓜原创 L0/L3 层。

不依赖任何第三方 Agent Skill 正文；配置来自 data/skills/contracts/。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.paths import skills_data_dir

CONTRACTS_DIR = skills_data_dir() / "contracts"
PACING_DIR = skills_data_dir() / "pacing_profiles"
STORY_TYPES_DIR = skills_data_dir() / "story_types"

# 阶段枚举
STAGE_SCRIPT = "script"
STAGE_CAST = "cast"
STAGE_SHOT_BREAK = "shot_break"
STAGE_IDENTITY = "identity"
STAGE_SCENE_ENV = "scene_env"  # 场景造景：纯环境，禁止人物
STAGE_SHOT_IMAGE = "shot_image"
STAGE_VIDEO = "video"
STAGE_AUDIT = "audit"

VALID_STAGES = frozenset(
    {
        STAGE_SCRIPT,
        STAGE_CAST,
        STAGE_SHOT_BREAK,
        STAGE_IDENTITY,
        STAGE_SCENE_ENV,
        STAGE_SHOT_IMAGE,
        STAGE_VIDEO,
        STAGE_AUDIT,
    }
)

VALID_PACING = frozenset(
    {
        "pace_balanced",
        "pace_dense",
        "pace_suspense",
        "pace_spectacle",
        "pace_comedy",
        "pace_period",
        "pace_whimsy",
    }
)

COMPAT_LEVELS = frozenset({"recommend", "allow", "caution", "forbid"})


def _simple_yaml_load(text: str) -> dict[str, Any]:
    """极简 YAML 子集解析（无第三方依赖）：支持扁平 key、列表、一层嵌套 map。"""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list]] = [(0, root)]
    pending_key: str | None = None
    pending_indent = 0

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        # pop stack to current indent
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if line.startswith("- "):
            item = line[2:].strip().strip('"').strip("'")
            if isinstance(parent, list):
                parent.append(_coerce(item))
            elif pending_key is not None and isinstance(parent, dict):
                lst = parent.setdefault(pending_key, [])
                if not isinstance(lst, list):
                    lst = []
                    parent[pending_key] = lst
                lst.append(_coerce(item))
                if stack[-1][0] != indent:
                    stack.append((indent, lst))
            continue

        if ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if not isinstance(parent, dict):
                continue
            if rest == "" or rest == "|" or rest == ">":
                # nested map or list follows
                parent[key] = {}
                stack.append((indent + 2, parent[key]))
                pending_key = key
                pending_indent = indent
            elif rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                parent[key] = [
                    _coerce(x.strip().strip('"').strip("'"))
                    for x in inner.split(",")
                    if x.strip()
                ]
                pending_key = None
            else:
                parent[key] = _coerce(rest.strip('"').strip("'"))
                pending_key = key
                pending_indent = indent
    return root


def _coerce(value: str) -> Any:
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_json_or_yaml(stem: str, default: dict[str, Any]) -> dict[str, Any]:
    """优先 JSON（无第三方依赖），兼容同名 yaml 文档。"""
    import json

    json_path = CONTRACTS_DIR / f"{stem}.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else default
        except json.JSONDecodeError:
            pass
    yaml_path = CONTRACTS_DIR / f"{stem}.yaml"
    if yaml_path.is_file():
        try:
            return _simple_yaml_load(yaml_path.read_text(encoding="utf-8")) or default
        except Exception:  # noqa: BLE001
            return default
    return default


def load_platform() -> dict[str, Any]:
    return _load_json_or_yaml(
        "platform",
        {
            "version": 1,
            "max_shot_duration_sec": 5,
            "min_shot_duration_sec": 3,
            "max_style_prefix_chars": 800,
            "max_image_constraint_chars": 900,
            "default_aspect": "9:16",
        },
    )


def load_style_matrix() -> dict[str, Any]:
    return _load_json_or_yaml(
        "style_matrix",
        {"version": 1, "visual_families": {}, "matrix": {}},
    )


@dataclass
class StyleContract:
    """可注入各阶段的机读风格契约。"""

    version: int = 1
    visual_pack: str | None = None  # art style skill_key
    visual_name: str | None = None
    narrative_tag: str | None = None  # story_type key
    pacing_profile: str = "pace_balanced"
    aspect: str = "9:16"
    art_style_id: int | None = None
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    shot_bias: dict[str, float] = field(default_factory=dict)
    prompt_suffix: str = ""
    stage: str = STAGE_SHOT_IMAGE
    stage_notes: str = ""
    direction_hint: str = ""
    pacing_hint: str = ""
    narrative_hint: str = ""
    video_tags: str = ""
    compat_level: str = "allow"
    compat_message: str = ""

    def system_prefix(self, *, max_chars: int | None = None) -> str:
        """拼给 LLM system 的风格纪律段。"""
        platform = load_platform()
        limit = max_chars or int(platform.get("max_style_prefix_chars") or 800)
        parts: list[str] = [
            "【西瓜风格纪律】",
            f"画幅={self.aspect}；单镜时长 {platform.get('min_shot_duration_sec', 3)}"
            f"–{platform.get('max_shot_duration_sec', 5)} 秒；提示词优先中文；禁止水印/UI/字幕条。",
        ]
        if self.visual_name or self.visual_pack:
            parts.append(f"画风：{self.visual_name or self.visual_pack}")
        if self.must_include:
            parts.append("必带视觉锚词：" + "、".join(self.must_include[:12]))
        if self.must_exclude:
            parts.append("禁用：" + "、".join(self.must_exclude[:12]))
        if self.shot_bias:
            ratio = "、".join(f"{k}约{int(v * 100)}%" for k, v in self.shot_bias.items())
            parts.append(f"景别偏好：{ratio}")
        if self.pacing_hint:
            parts.append(f"节奏取向：{self.pacing_hint[:200]}")
        if self.narrative_hint:
            parts.append(f"题材导演：{self.narrative_hint[:200]}")
        if self.direction_hint:
            parts.append(f"分镜方法：{self.direction_hint[:240]}")
        if self.stage_notes:
            parts.append(self.stage_notes)
        if self.prompt_suffix and self.stage in (STAGE_SHOT_IMAGE, STAGE_IDENTITY, STAGE_VIDEO, STAGE_SHOT_BREAK):
            parts.append(f"画风后缀：{self.prompt_suffix[:160]}")
        text = "\n".join(p for p in parts if p)
        if len(text) > limit:
            return text[: limit - 1].rstrip() + "…"
        return text

    def image_constraint_block(self, *, max_chars: int | None = None) -> str:
        """拼给出图/出视频正向提示的风格段。"""
        platform = load_platform()
        limit = max_chars or int(platform.get("max_image_constraint_chars") or 900)
        chunks: list[str] = []
        if self.must_include:
            chunks.append("，".join(self.must_include))
        if self.prompt_suffix:
            chunks.append(self.prompt_suffix)
        if self.stage_notes:
            chunks.append(self.stage_notes)
        if self.must_exclude:
            chunks.append("禁止：" + "、".join(self.must_exclude[:10]))
        if self.stage == STAGE_VIDEO and self.video_tags:
            chunks.append(self.video_tags)
        text = "。".join(c.strip("。") for c in chunks if c and c.strip())
        if len(text) > limit:
            return text[: limit - 1].rstrip() + "…"
        return text

    def to_bible_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "visual_pack": self.visual_pack,
            "visual_name": self.visual_name,
            "narrative_tag": self.narrative_tag,
            "pacing_profile": self.pacing_profile,
            "aspect": self.aspect,
            "art_style_id": self.art_style_id,
        }

    def scan_excludes(self, text: str) -> list[str]:
        """返回命中的禁用词（简单子串）。"""
        hits: list[str] = []
        blob = text or ""
        for word in self.must_exclude:
            w = (word or "").strip()
            if w and w in blob:
                hits.append(w)
        return hits

    def missing_includes(self, text: str) -> list[str]:
        """返回未出现的锚词（宽松：任一子串命中即可）。"""
        blob = text or ""
        missing: list[str] = []
        for word in self.must_include:
            w = (word or "").strip()
            if not w:
                continue
            # 短锚词：整词出现；或拆成逗号已在列表中
            if w not in blob:
                missing.append(w)
        return missing


def parse_bible(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    text = (raw or "").strip()
    if not text:
        return {}
    import json

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_anchors_from_manual(manual: str | None) -> list[str]:
    """从 constraint 手册「出图必带词」行提取锚词。"""
    if not manual:
        return []
    # 反引号整段
    for m in re.finditer(r"`([^`]+)`", manual):
        chunk = m.group(1)
        if "，" in chunk or "," in chunk:
            parts = re.split(r"[，,]", chunk)
            return [p.strip() for p in parts if p.strip()][:12]
    # 「必带词」后的一行
    for line in manual.splitlines():
        if "必带" in line or "锚词" in line:
            parts = re.split(r"[，,、]", re.sub(r"[`*#]", "", line))
            cleaned = [p.strip() for p in parts if p.strip() and len(p.strip()) < 20]
            # drop section title words
            cleaned = [c for c in cleaned if c not in ("出图必带词", "将下列中文锚词写入正向提示", "2")]
            if len(cleaned) >= 2:
                return cleaned[:12]
    return []


def extract_bans_from_manual(manual: str | None) -> list[str]:
    if not manual:
        return []
    bans: list[str] = []
    in_section = False
    for line in manual.splitlines():
        if re.match(r"^##\s*6", line) or "禁用" in line[:12]:
            in_section = True
            # same line content after 禁用
            if "：" in line or ":" in line:
                rest = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
                if rest and not rest.startswith("#"):
                    bans.extend(re.split(r"[；;，,、]", rest))
            continue
        if in_section:
            if line.startswith("##"):
                break
            text = line.strip("- ").strip()
            if text:
                bans.extend(re.split(r"[；;，,、]", text))
    cleaned = []
    for b in bans:
        b = b.strip()
        if b and len(b) < 40 and b not in cleaned:
            cleaned.append(b)
    return cleaned[:16]


def load_pacing_hint(profile: str) -> str:
    key = (profile or "pace_balanced").strip()
    if key not in VALID_PACING:
        key = "pace_balanced"
    path = PACING_DIR / key / "SKILL.md"
    if not path.is_file():
        # also allow flat file
        path = PACING_DIR / f"{key}.md"
    if not path.is_file():
        return _default_pacing_hint(key)
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    # 取前 12 非空行
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return " ".join(lines[:12])[:400]


def _default_pacing_hint(key: str) -> str:
    defaults = {
        "pace_balanced": "节奏均衡：开场尽快交代人物关系，中段推进冲突，结尾留可续钩子。",
        "pace_dense": "密集体冲突：开场尽快进入压力事件；对白短句优先；每场至少一个可拍冲突点。",
        "pace_suspense": "悬念推进：信息分批释放；镜头保留未说完的线索；避免当场全部揭晓。",
        "pace_spectacle": "强视觉奇观：每场至少一个非常规视觉信息点；对白服务画面而非长篇解释。",
        "pace_comedy": "轻喜夸张：表情与动作可适度放大；笑点尽量短而可剪。",
        "pace_period": "年代质感：服化道与口语贴合设定年代；避免明显现代网络用语。",
        "pace_whimsy": "概念/拟态向：造型与材质可风格化；避免写实摄影质感与真人皮肤。",
    }
    return defaults.get(key, defaults["pace_balanced"])


def load_narrative_hint(tag: str | None) -> str:
    if not tag:
        return ""
    key = tag.strip()
    path = STORY_TYPES_DIR / key / "SKILL.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return " ".join(lines[:10])[:360]


def list_pacing_profiles() -> list[dict[str, str]]:
    """供 API / 前端选择。"""
    catalog = [
        ("pace_balanced", "均衡叙事", "通用兜底，冲突与信息分配平稳"),
        ("pace_dense", "密集体冲突", "高压对峙、短对白、冲突密度高"),
        ("pace_suspense", "悬念递进", "线索分批释放，适合推理与伏笔"),
        ("pace_spectacle", "强视觉奇观", "画面冲击优先，适合奇幻/动作向"),
        ("pace_comedy", "轻喜节奏", "短笑点、表情动作可夸张"),
        ("pace_period", "年代质感", "服化道与口语贴合年代设定"),
        ("pace_whimsy", "概念拟态", "非写实造型，禁止纯实拍质感"),
    ]
    return [
        {"key": k, "display_name": name, "description": desc}
        for k, name, desc in catalog
    ]


def list_story_types() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not STORY_TYPES_DIR.is_dir():
        return rows
    for folder in sorted(STORY_TYPES_DIR.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        md = folder / "SKILL.md"
        display = folder.name
        desc = ""
        if md.is_file():
            text = md.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                meta_block = parts[1] if len(parts) >= 2 else ""
                for line in meta_block.splitlines():
                    if line.strip().startswith("display_name:"):
                        display = line.split(":", 1)[1].strip()
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
        rows.append({"key": folder.name, "display_name": display, "description": desc})
    return rows


def check_compat(pacing: str, visual_pack: str | None) -> tuple[str, str]:
    """返回 (level, message)。"""
    matrix = load_style_matrix()
    families = matrix.get("visual_families") or {}
    grid = matrix.get("matrix") or {}
    pacing_key = pacing if pacing in VALID_PACING else "pace_balanced"
    family = None
    if visual_pack:
        family = families.get(visual_pack) or families.get(str(visual_pack))
    if not family:
        return "allow", ""
    row = grid.get(pacing_key) or {}
    level = row.get(family) or "allow"
    if level not in COMPAT_LEVELS:
        level = "allow"
    messages = {
        "recommend": "画风与节奏取向匹配良好",
        "allow": "画风与节奏取向可用",
        "caution": "画风与节奏取向可能冲突，建议确认后再生成",
        "forbid": "画风与节奏取向不兼容，请更换组合",
    }
    return str(level), messages.get(str(level), "")
