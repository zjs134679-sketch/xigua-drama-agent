"""全局配置（pydantic-settings）。环境变量前缀 XIGUA_，也可用 backend/.env 覆盖。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（本文件在 backend/app/core/config.py）
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DICT_DIR = BASE_DIR / "dict"
WORKFLOWS_DIR = BASE_DIR / "app" / "workflows"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XIGUA_", env_file=".env", extra="ignore")

    app_name: str = "西瓜短剧Agent（国内版）"
    version: str = "0.1.0"

    # 路径
    data_dir: Path = DATA_DIR
    dict_dir: Path = DICT_DIR
    workflows_dir: Path = WORKFLOWS_DIR
    # 加密 DB（SQLCipher）成品化时切换；当前先用普通 SQLite
    db_url: str = f"sqlite:///{(DATA_DIR / 'xigua_guonei.db').as_posix()}"

    # 本地 ComfyUI 默认算力节点
    comfyui_base_url: str = "http://127.0.0.1:8188"

    # 云端认证服务（注册/登录/封号计数）
    auth_server_url: str = "http://127.0.0.1:8100"

    # 合规：累计多少次红线违规后封号
    ban_threshold: int = 3

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
    ]


settings = Settings()

# 确保运行期目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
DICT_DIR.mkdir(parents=True, exist_ok=True)
