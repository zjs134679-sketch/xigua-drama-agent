"""画风库种子 —— 从 data/skills/art_styles 同步预设到 art_styles 表。"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import ArtStyle
from app.core.paths import art_styles_dir

ART_STYLES_DIR = art_styles_dir()

# 每次更换/重写 data/skills/art_styles 内容时递增；启动时若不一致则强制刷新「系统/旧」手册
ART_STYLES_PACK_VERSION = "xg-art-v1"

# 版本标记文件（与技能包同目录，便于随仓库分发）
_SEED_VERSION_FILE = ART_STYLES_DIR / ".seed_version"

_XIGUA_SYSTEM_MARKERS: tuple[str, ...] = (
    "西瓜画风约束",
    "西瓜短剧默认兜底",
    "西瓜短剧 Agent 原创画风",
    "西瓜短剧原创画风",
)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict = {}
    for line in parts[1].strip().split("\n"):
        m = re.match(r"^([\w_-]+):\s*(.*)", line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "tags":
            meta[key] = [t.strip() for t in value.strip("[]").split(",") if t.strip()]
        elif key == "sort_order":
            try:
                meta[key] = int(value)
            except ValueError:
                meta[key] = 0
        else:
            meta[key] = value
    return meta, parts[2].strip()


def load_preset_art_styles() -> list[dict]:
    """读取技能目录中的画风预设（文件驱动）。

    约束手册优先 `constraint.md`，兼容旧名 `prefix.md`；再回退 SKILL.md 正文。
    """
    presets: list[dict] = []
    if not ART_STYLES_DIR.is_dir():
        return presets
    for folder in sorted(ART_STYLES_DIR.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        md = folder / "SKILL.md"
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        name = meta.get("display_name") or meta.get("name") or folder.name
        constraint_path = folder / "constraint.md"
        prefix_path = folder / "prefix.md"
        readme_path = folder / "README.md"
        if constraint_path.is_file():
            constraint = constraint_path.read_text(encoding="utf-8").strip()
        elif prefix_path.is_file():
            constraint = prefix_path.read_text(encoding="utf-8").strip()
        else:
            constraint = ""
        # prefix/constraint 过短时回退 README / SKILL 正文
        if len(constraint) < 80 and readme_path.is_file():
            readme = readme_path.read_text(encoding="utf-8").strip()
            if len(readme) > len(constraint):
                constraint = readme
        if len(constraint) < 80 and body:
            constraint = body
        # 统计包内文件，供 API/前端展示完整度
        file_count = sum(1 for _ in folder.rglob("*.md"))
        presets.append(
            {
                "skill_key": folder.name,
                "name": name,
                "prompt_suffix": meta.get("prompt_suffix") or "",
                "constraint_manual": constraint,
                "sort_order": int(meta.get("sort_order", 0) or 0),
                "description": meta.get("description") or "",
                "tags": meta.get("tags") or [],
                "file_count": file_count,
                "has_prefix": constraint_path.is_file() or prefix_path.is_file(),
                "has_art_prompt": (folder / "prompts").is_dir() or (folder / "art_prompt").is_dir(),
            }
        )
    presets.sort(key=lambda p: (p["sort_order"], p["name"]))
    return presets


def read_seed_version() -> str:
    if not _SEED_VERSION_FILE.is_file():
        return ""
    try:
        return _SEED_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_seed_version(version: str | None = None) -> None:
    ver = version or ART_STYLES_PACK_VERSION
    try:
        ART_STYLES_DIR.mkdir(parents=True, exist_ok=True)
        _SEED_VERSION_FILE.write_text(ver + "\n", encoding="utf-8")
    except OSError:
        pass


def is_xigua_system_manual(text: str | None) -> bool:
    cur = (text or "").strip()
    if not cur:
        return False
    return any(m in cur for m in _XIGUA_SYSTEM_MARKERS)


def _is_thin_system_manual(current: str, pack: str) -> bool:
    """判断是否为系统精简种子（可安全升级到完整手册），避免覆盖用户手写约束。"""
    cur = (current or "").strip()
    pack = (pack or "").strip()
    if not pack or not cur:
        return bool(pack and not cur)
    if len(cur) >= max(400, int(len(pack) * 0.45)):
        return False
    markers = (
        "西瓜短剧默认兜底",
        "西瓜画风约束",
        "出图锚定词（prompt 必带）",
        "出图必带词",
        "很短的约束",
        "# 写实电影感（通用默认）",
        "# 真人都市写实",
    )
    return any(m in cur for m in markers)


def _should_force_row(
    cur_manual: str,
    pack_manual: str,
    *,
    force: bool,
    pack_version_bumped: bool,
    refresh_empty_manual: bool,
    refresh_short_manual: bool,
) -> tuple[bool, str]:
    """返回 (是否 force 覆盖手册/后缀, 原因)。"""
    if force:
        return True, "force"
    if refresh_empty_manual and not cur_manual and pack_manual:
        return True, "empty"
    if refresh_short_manual and _is_thin_system_manual(cur_manual, pack_manual):
        return True, "thin_system"
    # 技能包版本升级：仅刷新西瓜系统手册，不碰用户手写
    if pack_version_bumped and pack_manual:
        if is_xigua_system_manual(cur_manual):
            return True, "pack_bump_system"
        if not cur_manual:
            return True, "pack_bump_empty"
    return False, ""


def seed_preset_art_styles(
    db: Session,
    *,
    refresh_empty_manual: bool = True,
    force: bool = False,
    refresh_short_manual: bool = True,
    write_version: bool = True,
    **_ignored: object,
) -> dict:
    """
    幂等种子：
    - 按中文 name 匹配；不存在则插入
    - force=True：用技能包覆盖 suffix / constraint_manual / sort_order
    - 技能包版本号变化时：force 刷新西瓜系统手册，保留用户手写
    - refresh_empty_manual：空约束/空后缀补全
    - refresh_short_manual：库内手册明显短于技能包时自动补全（仅系统精简版）
    """
    presets = load_preset_art_styles()
    if not presets:
        return {
            "inserted": 0,
            "updated": 0,
            "total_presets": 0,
            "forced": force,
            "pack_version": ART_STYLES_PACK_VERSION,
            "pack_version_bumped": False,
        }

    stored_version = read_seed_version()
    pack_version_bumped = stored_version != ART_STYLES_PACK_VERSION

    existing = {row.name: row for row in db.scalars(select(ArtStyle)).all()}
    inserted = 0
    updated = 0
    force_reasons: dict[str, int] = {}

    for p in presets:
        row = existing.get(p["name"])
        if row is None:
            db.add(
                ArtStyle(
                    name=p["name"],
                    prompt_suffix=p["prompt_suffix"],
                    lora=None,
                    thumbnail=None,
                    sort_order=p["sort_order"],
                    constraint_manual=p["constraint_manual"],
                )
            )
            inserted += 1
            continue

        changed = False
        pack_manual = p["constraint_manual"] or ""
        pack_suffix = p["prompt_suffix"] or ""
        cur_manual = (row.constraint_manual or "").strip()
        cur_suffix = (row.prompt_suffix or "").strip()

        do_force, reason = _should_force_row(
            cur_manual,
            pack_manual,
            force=force,
            pack_version_bumped=pack_version_bumped,
            refresh_empty_manual=refresh_empty_manual,
            refresh_short_manual=refresh_short_manual,
        )

        if do_force:
            if pack_manual and cur_manual != pack_manual:
                row.constraint_manual = pack_manual
                changed = True
            if pack_suffix and cur_suffix != pack_suffix:
                row.prompt_suffix = pack_suffix
                changed = True
            if changed:
                force_reasons[reason] = force_reasons.get(reason, 0) + 1
        else:
            # 非 force：仅补空后缀
            if refresh_empty_manual and not cur_suffix and pack_suffix:
                row.prompt_suffix = pack_suffix
                changed = True

        if row.sort_order != p["sort_order"]:
            row.sort_order = p["sort_order"]
            changed = True
        if changed:
            updated += 1

    if inserted or updated:
        db.commit()

    if write_version and (inserted or updated or pack_version_bumped or not stored_version):
        write_seed_version(ART_STYLES_PACK_VERSION)

    return {
        "inserted": inserted,
        "updated": updated,
        "total_presets": len(presets),
        "forced": force,
        "pack_version": ART_STYLES_PACK_VERSION,
        "pack_version_prev": stored_version or None,
        "pack_version_bumped": pack_version_bumped,
        "force_reasons": force_reasons,
        "packs": [
            {
                "skill_key": p["skill_key"],
                "name": p["name"],
                "file_count": p["file_count"],
                "has_prefix": p["has_prefix"],
                "has_art_prompt": p["has_art_prompt"],
            }
            for p in presets
        ],
    }
