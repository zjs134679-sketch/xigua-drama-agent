"""运行路径解析：开发仓库 vs Nuitka/安装包冻结。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Nuitka/PyInstaller 冻结检测（子模块内 __compiled__ 不可见，靠 exe 旁资源判断）。"""
    if getattr(sys, "frozen", False):
        return True
    if globals().get("__compiled__") is not None:
        return True
    exe_dir = Path(sys.executable).resolve().parent
    return (exe_dir / "web" / "index.html").is_file() or (exe_dir / "xigua-backend.exe").is_file()


def app_base_dir() -> Path:
    """可执行文件目录（冻结/安装包）或 backend/ 目录（开发）。"""
    exe_dir = Path(sys.executable).resolve().parent
    if is_frozen() or (exe_dir / "web" / "index.html").is_file():
        return exe_dir
    return Path(__file__).resolve().parents[2]


def skills_data_dir() -> Path:
    """画风 + 故事类型技能根目录（含 art_styles / story_types）。"""
    env = os.environ.get("XIGUA_SKILLS_DIR")
    if env:
        return Path(env)
    base = app_base_dir()
    for candidate in (
        base / "skills",
        base / "data" / "skills",
        base.parent / "data" / "skills",  # 开发：仓库 data/skills
    ):
        if candidate.is_dir():
            return candidate
    return base.parent / "data" / "skills"


def art_styles_dir() -> Path:
    return skills_data_dir() / "art_styles"


def frontend_dist_dir() -> Path | None:
    env = os.environ.get("XIGUA_FRONTEND_DIST")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    base = app_base_dir()
    candidates.extend(
        [
            base / "web",
            base / "frontend" / "dist",
            base.parent / "frontend" / "dist",
        ]
    )
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return None
