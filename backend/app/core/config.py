"""全局配置（pydantic-settings）。环境变量前缀 XIGUA_，也可用 backend/.env 覆盖。"""
from __future__ import annotations

import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（本文件在 backend/app/core/config.py）
# Nuitka onefile：优先用 exe 旁目录（web/skills/dict），否则开发态 backend/
_exe_dir = Path(sys.executable).resolve().parent
_dev_backend = Path(__file__).resolve().parents[2]
if (_exe_dir / "web" / "index.html").is_file() or (_exe_dir / "xigua-backend.exe").is_file() or getattr(sys, "frozen", False):
    BASE_DIR = _exe_dir
else:
    BASE_DIR = _dev_backend
DATA_DIR = BASE_DIR / "data"
# dict：exe 旁优先，其次冻结包内/开发 backend/dict
_dict_candidates = (
    BASE_DIR / "dict",
    _dev_backend / "dict",
)
DICT_DIR = next((p for p in _dict_candidates if p.is_dir()), BASE_DIR / "dict")
_wf_candidates = (
    BASE_DIR / "app" / "workflows",
    _dev_backend / "app" / "workflows",
)
WORKFLOWS_DIR = next((p for p in _wf_candidates if p.is_dir()), BASE_DIR / "app" / "workflows")
# 前端静态资源（安装包内 frontend/dist 或与 exe 同级 web）
FRONTEND_DIST = Path(
    __import__("os").environ.get("XIGUA_FRONTEND_DIST", str(BASE_DIR / "web"))
)
REPO_SKILLS = Path(
    __import__("os").environ.get(
        "XIGUA_SKILLS_DIR",
        str(BASE_DIR / "skills" if (BASE_DIR / "skills").is_dir() else BASE_DIR.parent / "data" / "skills"),
    )
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="XIGUA_", env_file=".env", extra="ignore")

    app_name: str = "西瓜短剧Agent（国内版）"
    version: str = "0.1.0"

    # 路径（可用 XIGUA_DATA_DIR 覆盖，安装包写到安装目录 data）
    data_dir: Path = Path(__import__("os").environ.get("XIGUA_DATA_DIR", str(DATA_DIR)))
    dict_dir: Path = DICT_DIR
    workflows_dir: Path = WORKFLOWS_DIR
    # 加密 DB（SQLCipher）成品化时切换；当前先用普通 SQLite
    db_url: str = f"sqlite:///{(Path(__import__('os').environ.get('XIGUA_DATA_DIR', str(DATA_DIR))) / 'xigua_guonei.db').as_posix()}"

    # 本地 ComfyUI 默认算力节点
    comfyui_base_url: str = "http://127.0.0.1:8188"

    # 云端认证服务（注册/登录/封号/授权加密）
    auth_server_url: str = "http://127.0.0.1:8100"
    # 能力票验签公钥（Ed25519，base64，32 字节原始公钥）：由构建脚本从 auth-server 私钥派生填入。
    # 客户端不再持有任何签名密钥（A2 修复）；旧 HS256 能力票作废。
    capability_pubkey: str = ""
    # 遗留字段：auth-server 访问令牌仍用该密钥 HS256 签发（服务端自验）；客户端不再需要它。
    auth_secret: str = "dev-secret-change-me-in-prod"
    # 生产打包设 true：生成类接口要求有效 JWT/能力票 且 license_active
    license_enforce: bool = False

    # 合规：累计多少次红线违规后封号
    ban_threshold: int = 3

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
    ]


settings = Settings()

# 确保运行期目录存在（安装到 Program Files 时可能失败，勿在 import 阶段崩溃）
try:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
try:
    DICT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
