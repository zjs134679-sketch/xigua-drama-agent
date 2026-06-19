"""导入所有模型，确保 Base.metadata 注册完整（供 init_db 建表）。"""
from app.models import domain, system  # noqa: F401
from app.models.domain import (  # noqa: F401
    AgentConfig,
    AiServiceConfig,
    AiServiceProvider,
    AiVoice,
    Asset,
    Character,
    Drama,
    Episode,
    ImageGeneration,
    Prop,
    Scene,
    Storyboard,
    VideoGeneration,
    VideoMerge,
)
from app.models.system import ComputeNode, User, Violation  # noqa: F401
