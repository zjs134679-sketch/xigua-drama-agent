"""Frozen backend entry point for the desktop application."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def _install_base() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _writable_user_dir() -> Path:
    """用户可写目录：避免装在 Program Files 时写库失败。"""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(local) / "XiguaDramaAgent"


def _load_config_env(cfg: Path) -> None:
    if not cfg.is_file():
        return
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _bootstrap_env() -> None:
    """安装目录 config.env + 前端/技能路径；数据写入 LocalAppData。"""
    base = _install_base()
    _load_config_env(base / "config.env")

    web = base / "web"
    skills = base / "skills"
    if web.is_dir() and "XIGUA_FRONTEND_DIST" not in os.environ:
        os.environ["XIGUA_FRONTEND_DIST"] = str(web)
    if skills.is_dir() and "XIGUA_SKILLS_DIR" not in os.environ:
        os.environ["XIGUA_SKILLS_DIR"] = str(skills)

    user_root = _writable_user_dir()
    data = user_root / "data"
    logs = user_root / "logs"
    try:
        data.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 回退到安装目录 data（可能只读）
        data = base / "data"
        logs = base / "logs"
        data.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XIGUA_DATA_DIR", str(data))
    os.environ.setdefault("XIGUA_LOG_DIR", str(logs))

    # 正式包默认强制授权（可被 config.env 覆盖）
    os.environ.setdefault("XIGUA_LICENSE_ENFORCE", "true")
    os.environ.setdefault("XIGUA_AUTH_SERVER_URL", "http://127.0.0.1:8100")


def _ensure_stdio() -> None:
    """无控制台/重定向时 stdout/stderr 可能为 None，会导致 uvicorn 日志 isatty 崩溃。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _uvicorn_log_config() -> dict:
    """避免 DefaultFormatter 在无 TTY 环境下访问 stream.isatty() 失败。"""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(levelname)s: %(message)s"},
            "access": {"format": "%(message)s"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }


def main() -> None:
    _ensure_stdio()
    _bootstrap_env()
    log_dir = Path(os.environ.get("XIGUA_LOG_DIR") or (_writable_user_dir() / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        crash_log = log_dir / "backend-crash.log"
    except OSError:
        crash_log = Path(os.environ.get("TEMP", ".")) / "xigua-backend-crash.log"

    try:
        import uvicorn
        from app.main import app

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=5678,
            log_level="info",
            log_config=_uvicorn_log_config(),
            access_log=False,
        )
    except Exception:
        try:
            crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
