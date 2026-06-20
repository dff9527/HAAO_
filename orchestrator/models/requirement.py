from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequirementStatus(StrEnum):
    DRAFT = "draft"
    DECOMPOSING = "decomposing"
    PREVIEW_READY = "preview_ready"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class RequirementAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["path", "error_log", "figma", "url", "note"] = "note"
    value: str


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    id: str = Field(pattern=r"^R-[0-9]{3,}$")
    project_id: str | None = None
    prompt: str = Field(min_length=1)
    repo: str = "."
    branch: str = "main"
    scope_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high"] = "medium"
    intent: Literal["feature", "bugfix", "refactor", "chore", "spike"] = "feature"
    scale: Literal["small", "medium", "large"] | None = None
    granularity: Literal["coarse", "balanced", "fine"] = "balanced"
    allow_new_files: bool = False
    test_command: str = ""
    attachments: list[RequirementAttachment] = Field(default_factory=list)
    acceptance_notes: str = ""
    status: RequirementStatus = RequirementStatus.DRAFT
    proposed_tickets: list[dict] = Field(default_factory=list)
    generated_ticket_ids: list[str] = Field(default_factory=list)
    cloud_input_tokens: int = 0
    cloud_output_tokens: int = 0
    cloud_cost_usd: float = 0.0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)
