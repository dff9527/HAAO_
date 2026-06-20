from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "atomic_ticket.schema.json"


class SchemaValidationError(ValueError):
    """Raised when a ticket dictionary does not satisfy the JSON Schema."""


class TicketStatus(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    TESTING = "testing"
    DIFF_PENDING = "diff_pending"
    REVIEW = "review"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    DONE = "done"
    BLOCKED = "blocked"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class Task(StrictModel):
    description: str
    target_files: list[str] = Field(min_length=1, max_length=5)
    constraints: list[str] = Field(default_factory=list)


class ContextFile(StrictModel):
    path: str
    content: str
    truncated: bool = False
    reason: str | None = None


class Context(StrictModel):
    files: list[ContextFile]
    related_symbols: list[str] = Field(default_factory=list)
    notes: str = ""
    token_estimate: int | None = Field(default=None, ge=0)


class TestCommand(StrictModel):
    __test__ = False

    command: str
    expect: Literal["pass", "fail"] = "pass"
    timeout_sec: int = 120


class DefinitionOfDone(StrictModel):
    tests: list[TestCommand] = Field(min_length=1)
    static_checks: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class Execution(StrictModel):
    assigned_model: str
    retry_budget: int = Field(default=3, ge=0)
    attempts: int = Field(default=0, ge=0)
    escalate_to: Literal["tech_lead", "human", "blocked"] = "tech_lead"


class ResultLog(StrictModel):
    ts: datetime
    level: Literal["info", "warn", "error"] = "info"
    message: str


class Result(StrictModel):
    outcome: Literal["success", "test_failed", "error", "pending"] = "pending"
    diff: str | None = None
    test_output: str | None = None
    logs: list[ResultLog] = Field(default_factory=list)


class Audit(StrictModel):
    reviewed_by: str = ""
    verdict: Literal["pending", "approved", "rejected"] = "pending"
    feedback: str = ""


class Metadata(StrictModel):
    model_config = ConfigDict(extra="allow")

    created_at: datetime | None = None
    updated_at: datetime | None = None
    epic: str | None = None


class Ticket(StrictModel):
    id: str = Field(pattern=r"^T-[0-9]{3,}$")
    title: str = Field(max_length=120)
    type: Literal["feature", "bugfix", "refactor", "test", "chore"]
    status: TicketStatus
    priority: Literal["low", "medium", "high"] = "medium"
    created_by: str = "claude"
    dependencies: list[str] = Field(default_factory=list)
    task: Task
    context: Context
    definition_of_done: DefinitionOfDone
    execution: Execution
    result: Result | None = None
    audit: Audit
    metadata: Metadata | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        validate_ticket_schema(data)
        model = cls.model_validate(data)
        as_dict = model.to_dict()
        validate_ticket_schema(as_dict)
        return model

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def load_ticket_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
        return json.load(schema_file)


def validate_ticket_schema(data: dict[str, Any]) -> None:
    validator = Draft202012Validator(load_ticket_schema())
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        raise SchemaValidationError(_format_schema_error(errors[0])) from errors[0]


def _format_schema_error(error: JsonSchemaValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    location = path or "<root>"
    return f"Ticket schema validation failed at {location}: {error.message}"
