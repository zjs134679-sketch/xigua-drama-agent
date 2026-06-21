"""域模型 —— 从 xigua-drama 的 Drizzle schema.ts 移植（列名保持一致，便于将来导入旧数据）。

为新建库做了类型清理：时间戳用 DateTime，软删 deleted_at 保留。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin


class Drama(Base, TimestampMixin):
    __tablename__ = "dramas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str | None] = mapped_column(Text, default="realistic")
    total_episodes: Mapped[int] = mapped_column(Integer, default=1)
    total_duration: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="draft", nullable=False)
    thumbnail: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    extra_meta: Mapped[str | None] = mapped_column("metadata", Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Episode(Base, TimestampMixin):
    __tablename__ = "episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    script_content: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="draft")
    video_url: Mapped[str | None] = mapped_column(Text)
    thumbnail: Mapped[str | None] = mapped_column(Text)
    image_config_id: Mapped[int | None] = mapped_column(Integer)
    video_config_id: Mapped[int | None] = mapped_column(Integer)
    audio_config_id: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Character(Base, TimestampMixin):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    appearance: Mapped[str | None] = mapped_column(Text)
    personality: Mapped[str | None] = mapped_column(Text)
    voice_style: Mapped[str | None] = mapped_column(Text)
    image_prompt: Mapped[str | None] = mapped_column(Text)  # 可编辑的出图提示词
    view_type: Mapped[str] = mapped_column(Text, default="full_body")  # 出图视角：full_body/headshot/side
    image_url: Mapped[str | None] = mapped_column(Text)
    reference_images: Mapped[str | None] = mapped_column(Text)
    seed_value: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int | None] = mapped_column(Integer)
    local_path: Mapped[str | None] = mapped_column(Text)
    voice_sample_url: Mapped[str | None] = mapped_column(Text)
    voice_provider: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class EpisodeCharacter(Base):
    __tablename__ = "episode_characters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EpisodeScene(Base):
    __tablename__ = "episode_scenes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    scene_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Scene(Base, TimestampMixin):
    __tablename__ = "scenes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    episode_id: Mapped[int | None] = mapped_column(Integer, index=True)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    time: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    storyboard_count: Mapped[int] = mapped_column(Integer, default=1)
    image_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    local_path: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Storyboard(Base, TimestampMixin):
    __tablename__ = "storyboards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    scene_id: Mapped[int | None] = mapped_column(Integer, index=True)
    storyboard_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    time: Mapped[str | None] = mapped_column(Text)
    shot_type: Mapped[str | None] = mapped_column(Text)
    angle: Mapped[str | None] = mapped_column(Text)
    movement: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    atmosphere: Mapped[str | None] = mapped_column(Text)
    image_prompt: Mapped[str | None] = mapped_column(Text)
    video_prompt: Mapped[str | None] = mapped_column(Text)
    bgm_prompt: Mapped[str | None] = mapped_column(Text)
    sound_effect: Mapped[str | None] = mapped_column(Text)
    dialogue: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    composed_image: Mapped[str | None] = mapped_column(Text)
    first_frame_image: Mapped[str | None] = mapped_column(Text)
    last_frame_image: Mapped[str | None] = mapped_column(Text)
    reference_images: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text)
    tts_audio_url: Mapped[str | None] = mapped_column(Text)
    subtitle_url: Mapped[str | None] = mapped_column(Text)
    composed_video_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class StoryboardCharacter(Base):
    __tablename__ = "storyboard_characters"
    storyboard_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class AiServiceConfig(Base, TimestampMixin):
    __tablename__ = "ai_service_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_type: Mapped[str] = mapped_column(Text, nullable=False)  # image/video/tts/llm
    provider: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    query_endpoint: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[str | None] = mapped_column(Text)


class AiServiceProvider(Base, TimestampMixin):
    __tablename__ = "ai_service_providers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    service_type: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    default_url: Mapped[str | None] = mapped_column(Text)
    preset_models: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AiVoice(Base):
    __tablename__ = "ai_voices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voice_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    voice_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentConfig(Base, TimestampMixin):
    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    max_iterations: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class ImageGeneration(Base, TimestampMixin):
    __tablename__ = "image_generations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    storyboard_id: Mapped[int | None] = mapped_column(Integer, index=True)
    drama_id: Mapped[int | None] = mapped_column(Integer, index=True)
    scene_id: Mapped[int | None] = mapped_column(Integer)
    character_id: Mapped[int | None] = mapped_column(Integer)
    prop_id: Mapped[int | None] = mapped_column(Integer)
    image_type: Mapped[str | None] = mapped_column(Text)
    frame_type: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[int | None] = mapped_column(Integer)
    cfg_scale: Mapped[float | None] = mapped_column(Float)
    seed: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(Text)
    minio_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    task_id: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    reference_images: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class VideoGeneration(Base, TimestampMixin):
    __tablename__ = "video_generations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    storyboard_id: Mapped[int | None] = mapped_column(Integer, index=True)
    drama_id: Mapped[int | None] = mapped_column(Integer, index=True)
    provider: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    image_gen_id: Mapped[int | None] = mapped_column(Integer)
    reference_mode: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    first_frame_url: Mapped[str | None] = mapped_column(Text)
    last_frame_url: Mapped[str | None] = mapped_column(Text)
    reference_image_urls: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(Text)
    aspect_ratio: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str | None] = mapped_column(Text)
    motion_level: Mapped[int | None] = mapped_column(Integer)
    camera_motion: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(Integer)
    video_url: Mapped[str | None] = mapped_column(Text)
    minio_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    task_id: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class VideoMerge(Base):
    __tablename__ = "video_merges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int | None] = mapped_column(Integer)
    drama_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    scenes: Mapped[str | None] = mapped_column(Text)
    merged_url: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Prop(Base, TimestampMixin):
    __tablename__ = "props"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    reference_images: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drama_id: Mapped[int | None] = mapped_column(Integer, index=True)
    episode_id: Mapped[int | None] = mapped_column(Integer)
    storyboard_id: Mapped[int | None] = mapped_column(Integer)
    storyboard_num: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(Text)  # image/video/audio
    category: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(Text)
    image_gen_id: Mapped[int | None] = mapped_column(Integer)
    video_gen_id: Mapped[int | None] = mapped_column(Integer)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArtStyle(Base, TimestampMixin):
    __tablename__ = "art_styles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_suffix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lora: Mapped[str | None] = mapped_column(Text)
    thumbnail: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
