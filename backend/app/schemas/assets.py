from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ArtStyleCreate(BaseModel):
    name: str = Field(min_length=1)
    prompt_suffix: str = ""
    lora: str | None = None
    thumbnail: str | None = None
    sort_order: int = 0


class ArtStyleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    prompt_suffix: str | None = None
    lora: str | None = None
    thumbnail: str | None = None
    sort_order: int | None = None


class CharacterGenerateRequest(BaseModel):
    character_id: int | None = None
    prompt: str | None = None
    art_style_id: int | None = None
    username: str | None = None
    scene_id: int | None = None
    action: str | None = None
    node_id: int | None = None
    resolution: str | None = None
    extra: str | None = None
    view_type: str = "full_body"  # 出图视角：full_body/headshot/side

    @model_validator(mode="after")
    def validate_target(self):
        if self.character_id is None and not self.prompt:
            raise ValueError("character_id 与 prompt 至少提供一项")
        return self


class SceneGenerateRequest(BaseModel):
    scene_id: int | None = None
    prompt: str | None = None
    art_style_id: int | None = None
    username: str | None = None
    node_id: int | None = None
    resolution: str | None = None
    extra: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.scene_id is None and not self.prompt:
            raise ValueError("scene_id 与 prompt 至少提供一项")
        return self


class PropGenerateRequest(BaseModel):
    prop_id: int | None = None
    prompt: str | None = None
    art_style_id: int | None = None
    username: str | None = None
    node_id: int | None = None
    resolution: str | None = None
    extra: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.prop_id is None and not self.prompt:
            raise ValueError("prop_id 与 prompt 至少提供一项")
        return self
