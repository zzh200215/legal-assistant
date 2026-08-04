from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    name: str
    description: str | None = None
    template: str
    variables: str | None = None
    change_note: str | None = Field(default=None, min_length=4)


class PromptTemplateVersionOut(BaseModel):
    id: int
    template_id: int
    version: int
    template: str
    is_active: bool
    is_rollout: bool = False
    traffic_percentage: int = 0
    change_note: str | None = None
    variables_schema: list[dict] = []
    experiment_refs: list[dict] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromptTemplateOut(PromptTemplateCreate):
    id: int
    active_version_id: int | None = None
    active_version_number: int | None = None
    previous_active_version_id: int | None = None
    previous_active_version_number: int | None = None
    rollout: dict | None = None
    variables_schema: list[dict] = []
    created_at: datetime
    updated_at: datetime
    versions: list[PromptTemplateVersionOut] = []

    model_config = ConfigDict(from_attributes=True)
