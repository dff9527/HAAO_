from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from clients.lmstudio import LMStudioClient
from clients.claude_po import list_available_claude_models
from clients.cloud_reasoner import BaseCloudReasoner
from clients.factory import (
    ANTHROPIC_ALIASES,
    OPENAI_COMPAT_BASE_URLS,
    make_cloud_reasoner,
    split_provider,
)
from clients.tech_lead import ClaudeTechLeadClient
from orchestrator.attachments import AttachmentStorage, AttachmentStorageError
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.auto_worker import auto_worker
from orchestrator.chat_flow import ChatMessage, ChatService, ReasonerTurn, WorkItem
from orchestrator.cloud_models import (
    CloudModelRegistryError,
    add_cloud_model,
    delete_cloud_model,
)
from orchestrator.cloud_reasoner_config import (
    api_key_for_model,
    api_key_for_provider,
    build_cloud_reasoner,
    cloud_model_inventory,
    default_anthropic_model_id,
    provider_options,
    selected_cloud_reasoner_id,
    validate_cloud_reasoner_id,
)
from orchestrator.config import Settings, get_settings
from orchestrator.context.conventions import detect_conventions, detect_test_command
from orchestrator.context.injector import ContextInjector
from orchestrator.db.sqlite import (
    AmbiguousTicketError,
    AuditRepository,
    ChatAttachment,
    ChatRepository,
    DuplicateTicketError,
    EvalRunRepository,
    IdentityRepository,
    IntegrationProvider,
    IntegrationRepository,
    NotificationRepository,
    ProjectRepository,
    RequirementRepository,
    RunnerRepository,
    RunnerTokenRecord,
    RequirementTemplateRepository,
    RunEventRepository,
    SettingsRepository,
    TicketDeletionError,
    TicketRepository,
    connect,
)
from orchestrator.escalation import EscalationService
from orchestrator.evals import EvalService
from orchestrator.dx import DemoSeedService, build_requirement_summary
from orchestrator.diff_review import DiffReviewService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_registry import execution_key, execution_registry
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.git_flow import GitTicketFlow, now_iso
from orchestrator.insights import InsightsService
from orchestrator.trust import (
    build_acceptance_summary,
    build_decision_center,
    build_requirement_signals,
    build_ticket_signals,
)
from orchestrator.manual_ticket_flow import (
    ManualTicketCreatePayload,
    ManualTicketError,
    ManualTicketService,
)
from orchestrator.model_policy import (
    ALLOW_CLOUD_EXECUTION_SETTINGS_KEY,
    local_execution_model,
    local_model_fallback_chain,
)
from orchestrator.local_models import (
    LocalModelEndpoint,
    cache_local_models,
    discover_local_models,
    get_local_model_endpoints,
    set_local_model_endpoints,
)
from orchestrator.models.project import Project
from orchestrator.models.requirement import Requirement, RequirementAttachment, RequirementStatus
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.model_instructions import normalize_model_settings_id
from orchestrator.notifications import get_notification_webhook, set_notification_webhook
from orchestrator.policies import ExecutionPolicy
from orchestrator.pr_flow import (
    AcceptanceGateError,
    DirtyWorkspaceError,
    MissingIntegrationError,
    PullRequestFlowError,
    PullRequestService,
)
from orchestrator.requirements_flow import RequirementService
from orchestrator.review_flow import ReviewService
from orchestrator.role_routing import role_routing_store
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.secrets_crypto import SecretEncryptionError
from orchestrator.state_machine import InvalidTransitionError, TicketStateService


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RequirementRequest(BaseModel):
    requirement: str = Field(min_length=1)
    repo_context: str = ""
    project_id: str | None = None


class RequirementDecomposeRequest(BaseModel):
    project_id: str | None = None
    prompt: str = Field(min_length=1)
    repo: str = "."
    branch: str = "main"
    scope_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    priority: str = "medium"
    intent: str = "feature"
    scale: str | None = None
    granularity: str = "balanced"
    allow_new_files: bool = False
    test_command: str = ""
    attachments: list[RequirementAttachment] = Field(default_factory=list)
    acceptance_notes: str = ""


class RequirementConfirmRequest(BaseModel):
    project_id: str | None = None
    tickets: list[dict] | None = None


class RequirementResponse(BaseModel):
    tickets: list[dict]


class RequirementPreviewResponse(BaseModel):
    requirement_id: str
    requirement: dict
    proposed_tickets: list[dict]


class RequirementConfirmResponse(BaseModel):
    requirement: dict
    tickets: list[dict]


class RequirementListResponse(BaseModel):
    requirements: list[dict]


class RequirementDetailResponse(BaseModel):
    requirement: dict


class RequirementSummaryResponse(BaseModel):
    summary: dict


class ChatMessageCreateRequest(BaseModel):
    project_id: str = "default"
    text: str = Field(min_length=1)
    attachment_ids: list[str] = Field(default_factory=list)


class ChatAttachmentResponse(BaseModel):
    id: str
    filename: str
    mime: str
    size: int
    kind: Literal["file", "image"]
    stored_path: str


class ChatMessageResponse(BaseModel):
    id: str
    project_id: str
    role: Literal["user", "agent", "system_report"]
    text: str
    segment_id: str
    created_at: str
    requirement_id: str | None = None
    ticket_id: str | None = None
    report_kind: Literal["done", "blocked", "needs_you"] | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    attachments: list[ChatAttachmentResponse] = Field(default_factory=list)


class ChatMessagesResponse(BaseModel):
    messages: list[ChatMessageResponse]


class ChatTurnResponse(ChatMessagesResponse):
    filed_requirement_ids: list[str] = Field(default_factory=list)


class ChatSegmentCreateRequest(BaseModel):
    project_id: str = "default"
    title: str = Field(min_length=1)


class ChatSegmentResponse(BaseModel):
    id: str
    project_id: str
    title: str
    summary: str
    created_at: str
    is_active: bool


class ChatSegmentsResponse(BaseModel):
    segments: list[ChatSegmentResponse]


def _extract_json_object(raw: str) -> str:
    """Best-effort extraction of one JSON object from model output.

    Tolerates markdown code fences and surrounding prose that smaller local models
    sometimes emit around the JSON.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _parse_reasoner_payload(raw: str) -> ReasonerTurn:
    try:
        payload = json.loads(_extract_json_object(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("Chat reasoner returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Chat reasoner response must be a JSON object")
    reply = payload.get("reply", "")
    if not isinstance(reply, str):
        raise ValueError("Chat reasoner reply must be a string")

    raw_items = payload.get("work_items", [])
    if not isinstance(raw_items, list):
        raise ValueError("Chat reasoner work_items must be a list")
    work_items: list[WorkItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("Chat reasoner work item must be an object")
        title = item.get("title", "")
        prompt = item.get("prompt", "")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Chat reasoner work item title cannot be empty")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Chat reasoner work item prompt cannot be empty")
        work_items.append(WorkItem(title=title.strip(), prompt=prompt.strip()))

    updated_summary = payload.get("updated_summary")
    if updated_summary is not None and not isinstance(updated_summary, str):
        raise ValueError("Chat reasoner updated_summary must be a string or null")
    return ReasonerTurn(
        reply=reply.strip(),
        work_items=work_items,
        updated_summary=updated_summary,
    )


# Ticket statuses that still represent "open" work the agent should be aware of.
_OPEN_TICKET_STATUSES = (
    TicketStatus.BACKLOG,
    TicketStatus.READY,
    TicketStatus.IN_PROGRESS,
    TicketStatus.REVIEW,
    TicketStatus.AWAITING_ACCEPTANCE,
)
_MAX_BOARD_TICKETS = 40


def _open_tickets_for_reasoner(ticket_repository: TicketRepository | None) -> list[dict]:
    if ticket_repository is None:
        return []
    project_id = getattr(ticket_repository, "project_id", None)
    try:
        tickets = ticket_repository.list(project_id=project_id)
    except Exception:
        return []
    open_states = {str(status) for status in _OPEN_TICKET_STATUSES}
    rows = [
        {"id": ticket.id, "status": str(ticket.status), "title": ticket.title}
        for ticket in tickets
        if str(ticket.status) in open_states
    ]
    return rows[:_MAX_BOARD_TICKETS]


class CloudChatReasoner:
    is_local = False

    def __init__(
        self,
        client: BaseCloudReasoner,
        ticket_repository: TicketRepository | None = None,
    ) -> None:
        self.client = client
        self.ticket_repository = ticket_repository

    def respond(
        self,
        *,
        summary: str,
        recent: list[ChatMessage],
        user_text: str,
    ) -> ReasonerTurn:
        self.client._ensure_ready()
        open_tickets = _open_tickets_for_reasoner(self.ticket_repository)
        raw = self.client._complete(
            _chat_reasoner_prompt(summary, recent, user_text, open_tickets)
        )
        return _parse_reasoner_payload(raw)


class LocalChatReasoner:
    is_local = True

    def __init__(
        self,
        client: LMStudioClient,
        model: str,
        ticket_repository: TicketRepository | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.ticket_repository = ticket_repository

    def respond(
        self,
        *,
        summary: str,
        recent: list[ChatMessage],
        user_text: str,
    ) -> ReasonerTurn:
        if not self.model:
            raise ValueError(
                "No local chat model is available. Start LM Studio (or a local "
                "endpoint), or switch the chat agent to cloud in Settings."
            )
        open_tickets = _open_tickets_for_reasoner(self.ticket_repository)
        prompt = _chat_reasoner_prompt(summary, recent, user_text, open_tickets)
        raw = self.client.chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": "You output only valid JSON objects."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return _parse_reasoner_payload(raw)


class RequirementServiceGateway:
    def __init__(
        self,
        service: RequirementService,
        *,
        ticket_repository: TicketRepository,
        requirement_repository: RequirementRepository,
        tech_lead: BaseCloudReasoner,
        project_repository: ProjectRepository,
        settings_repository: SettingsRepository,
        chat_repository: ChatRepository,
    ) -> None:
        self.service = service
        self.ticket_repository = ticket_repository
        self.requirement_repository = requirement_repository
        self.tech_lead = tech_lead
        self.project_repository = project_repository
        self.settings_repository = settings_repository
        self.chat_repository = chat_repository

    def file_backlog_proposal(
        self,
        *,
        project_id: str,
        title: str,
        prompt: str,
        attachment_ids: list[str] | None = None,
    ) -> str:
        service = self._service_for_project(project_id)
        attachments = [
            RequirementAttachment(type=attachment.kind, value=attachment.stored_path)
            for attachment in self.chat_repository.attachments_by_ids(
                project_id,
                attachment_ids or [],
            )
        ]
        missing_count = len(set(attachment_ids or [])) - len(attachments)
        if missing_count > 0:
            raise ValueError("One or more attachments were not found")
        requirement = Requirement(
            id=service.next_requirement_id(),
            project_id=project_id,
            prompt=f"{title.strip()}\n\n{prompt.strip()}".strip(),
            acceptance_notes="Filed from chat.",
            attachments=attachments,
            status=RequirementStatus.DRAFT,
        )
        result = service.decompose_preview(requirement)
        return result.requirement.id

    def _service_for_project(self, project_id: str) -> RequirementService:
        if project_id == getattr(self.service, "project_id", None):
            return self.service
        project = _resolve_project(self.project_repository, project_id)
        repo_root = Path(project.path)
        return RequirementService(
            self.ticket_repository.scoped(project.id),
            self.requirement_repository.scoped(project.id),
            self.tech_lead,
            repo_root=repo_root,
            project_id=project.id,
            context_injector=ContextInjector(repo_root),
            settings_repository=self.settings_repository,
        )


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    default_branch: str = "main"
    id: str | None = None


class ProjectSettingsRequest(BaseModel):
    env: dict[str, str] | None = None
    env_allowlist: list[str] | None = None
    test_allow_network: bool | None = None
    sandbox_mode: Literal["auto", "docker", "unshare", "none"] | None = None
    setup_cmd: str | None = None
    cleanup_cmd: str | None = None
    default_branch: str | None = None


class ProjectResponse(BaseModel):
    project: dict


class DemoSeedResponse(BaseModel):
    project: dict
    requirement: dict
    proposed_tickets: list[dict]


class ProjectConventionsResponse(BaseModel):
    project_id: str
    test_command: str
    conventions: str


class ProjectsResponse(BaseModel):
    projects: list[dict]


class DeleteProjectResponse(BaseModel):
    deleted: bool
    project_id: str


class RequirementTemplateRequest(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    scope_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class RequirementTemplateResponse(BaseModel):
    template: dict


class RequirementTemplatesResponse(BaseModel):
    templates: list[dict]


class RequirementTemplateDeleteResponse(BaseModel):
    deleted: bool
    id: str


class ManualTicketCreateRequest(BaseModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=120)
    type: Literal["feature", "bugfix", "refactor", "test", "chore"] = "feature"
    target_files: list[str] = Field(min_length=1, max_length=5)
    task_description: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    dod_tests: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    assigned_model: str | None = None


class MoveRequest(BaseModel):
    status: TicketStatus


class AssignModelRequest(BaseModel):
    model: str = Field(min_length=1)


class EscalateRequest(BaseModel):
    escalated_to: str | None = None
    reason: str = "manual_escalation"


class RejectRequest(BaseModel):
    feedback: str = Field(min_length=1)


class SplitTicketRequest(BaseModel):
    feedback: str = Field(min_length=1)


class AbandonTicketRequest(BaseModel):
    reason: str = Field(min_length=1)


class DiffRejectRequest(BaseModel):
    feedback: str = Field(min_length=1)


class UpdateTicketRequest(BaseModel):
    task_description: str | None = None
    task_target_files: list[str] | None = None
    dod_tests: list[str] | None = None
    assigned_model: str | None = None
    rerun: bool = False


class RoleRoutingRequest(BaseModel):
    routing: dict[str, str | list[str]]


class RoleRoutingResponse(BaseModel):
    routing: dict[str, str | list[str]]


class ClaudeModelRequest(BaseModel):
    model: str = Field(min_length=1)


class ClaudeModelResponse(BaseModel):
    model: str


class ClaudeModelsAvailableResponse(BaseModel):
    models: list[str]


class ClaudeConnectionTestRequest(BaseModel):
    api_key: str = ""
    model: str = ""


class ClaudeConnectionTestResponse(BaseModel):
    valid: bool
    message: str
    models: list[str] = Field(default_factory=list)


class CloudReasonerRequest(BaseModel):
    model_id: str = Field(min_length=1)


class CloudReasonerResponse(BaseModel):
    model_id: str
    provider: str
    providers: list[dict] = Field(default_factory=list)
    registry: list[dict] = Field(default_factory=list)


class CloudModelCreateRequest(BaseModel):
    label: str = ""
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class CloudModelResponse(BaseModel):
    id: str
    label: str
    provider: str
    model_id: str
    key_configured: bool
    deletable: bool = True


class CloudModelsResponse(BaseModel):
    models: list[CloudModelResponse]


class CloudModelDeleteResponse(BaseModel):
    deleted: bool
    model_id: str


class CloudModelTestRequest(BaseModel):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    api_key: str | None = None


class CloudModelTestResponse(BaseModel):
    ok: bool
    message: str


class CloudModelListRequest(BaseModel):
    provider: str = Field(min_length=1)
    api_key: str | None = None


class CloudModelListResponse(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    message: str = ""


class IntegrationCredentialRequest(BaseModel):
    provider: Literal["github", "gitlab", "slack"]
    token: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)
    label: str = ""
    id: str | None = None


class IntegrationCredentialResponse(BaseModel):
    provider: Literal["github", "gitlab", "slack"]
    id: str
    label: str
    scopes: list[str]
    configured: bool
    created_at: str
    updated_at: str


class IntegrationCredentialsResponse(BaseModel):
    integrations: list[IntegrationCredentialResponse]


class IntegrationDeleteResponse(BaseModel):
    deleted: bool
    provider: str
    id: str


class RunEventsResponse(BaseModel):
    events: list[dict]


class InsightsResponse(BaseModel):
    project_id: str
    range: Literal["7d", "30d", "all"]
    generated_at: str
    throughput: dict
    cycle_time: dict
    escalation_rate: dict
    local_vs_cloud: dict
    cost: dict
    time_to_first_pr: dict
    roi: dict
    model_scorecard: list[dict]


class DecisionsResponse(BaseModel):
    project_id: str
    generated_at: str
    groups: list[dict]
    counts: dict
    derived_only: bool


class TicketSignalsResponse(BaseModel):
    signals: dict


class RequirementSignalsResponse(BaseModel):
    signals: dict


class AcceptanceSummaryResponse(BaseModel):
    summary: dict


class EvalRunRequest(BaseModel):
    model_id: str = Field(min_length=1)
    task_set_id: str = "r102-smoke"
    trials: int = Field(default=1, ge=1, le=10)


class EvalRunResponse(BaseModel):
    eval_run: dict


class EvalRunsResponse(BaseModel):
    eval_runs: list[dict]


class EvalTaskSetsResponse(BaseModel):
    task_sets: list[dict]


class NotificationSettingsRequest(BaseModel):
    webhook_url: str = ""


class NotificationSettingsResponse(BaseModel):
    webhook_url: str


class NotificationsResponse(BaseModel):
    notifications: list[dict]
    unread_count: dict


class NotificationReadResponse(BaseModel):
    notification: dict
    unread_count: dict


class NotificationReadAllResponse(BaseModel):
    updated: int
    unread_count: dict


class MembershipUpsertRequest(BaseModel):
    user_id: str = Field(min_length=1)
    workspace_id: str = "default"
    role: Literal["owner", "admin", "member", "viewer"]
    email: str = ""
    display_name: str = ""


class MembershipResponse(BaseModel):
    membership: dict


class AuditEventsResponse(BaseModel):
    events: list[dict]
    next_cursor: int | None = None


class RunnerRegisterRequest(BaseModel):
    workspace_id: str = "default"
    label: str = Field(default="local-runner", min_length=1)


class RunnerRegisterResponse(BaseModel):
    runner: dict
    token: str


class RunnerJobCreateRequest(BaseModel):
    workspace_id: str = "default"
    ticket_id: str | None = None
    payload: dict = Field(default_factory=dict)


class RunnerJobResponse(BaseModel):
    job: dict | None = None


class RunnerHeartbeatResponse(BaseModel):
    runner: dict


class RunnerLeaseRequest(BaseModel):
    ttl_sec: int = Field(default=300, ge=1, le=3600)


class RunnerEventsRequest(BaseModel):
    events: list[dict]


class RunnerEventsResponse(BaseModel):
    accepted: int


class RunnerCompleteRequest(BaseModel):
    status: Literal["terminal"] = "terminal"
    result: dict = Field(default_factory=dict)


class CloudExecutionSettingsRequest(BaseModel):
    allow_cloud_execution_model: bool = False


class CloudExecutionSettingsResponse(BaseModel):
    allow_cloud_execution_model: bool


class TicketResponse(BaseModel):
    ticket: dict


class TicketsResponse(BaseModel):
    tickets: list[dict]


class OperationResponse(BaseModel):
    ticket: dict


class SplitTicketResponse(BaseModel):
    parent_id: str
    child_ticket_ids: list[str]
    ticket: dict
    children: list[dict] = Field(default_factory=list)


class PullRequestResponse(BaseModel):
    pr_url: str
    status: str


class DeleteTicketResponse(BaseModel):
    deleted: bool
    ticket_id: str


class OrchestratorRunRequest(BaseModel):
    max_cycles: int = Field(default=10, ge=1, le=100)
    allow_dirty_workspace: bool = False


class OrchestratorRunResponse(BaseModel):
    results: list[dict]


class AutoWorkerRequest(BaseModel):
    project_id: str | None = None
    interval_sec: float = Field(default=5.0, ge=1.0, le=3600.0)
    max_cycles_per_tick: int = Field(default=10, ge=1, le=100)
    max_workers: int = Field(default=1, ge=1, le=16)
    allow_dirty_workspace: bool = False


class AutoWorkerResponse(BaseModel):
    running: bool
    interval_sec: float
    max_cycles_per_tick: int
    allow_dirty_workspace: bool
    last_started_at: str | None
    last_run_at: str | None
    last_error: str
    last_skipped_reason: str = ""
    project_id: str | None = None
    max_workers: int = 1
    worker_statuses: list[dict] = Field(default_factory=list)


class TicketGraphResponse(BaseModel):
    project_id: str
    nodes: list[dict]
    edges: list[dict]


class LocalModelEndpointRequest(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""


class LocalModelEndpointsRequest(BaseModel):
    endpoints: list[LocalModelEndpointRequest] = Field(default_factory=list)


class LocalModelEndpointsResponse(BaseModel):
    endpoints: list[dict]


class LocalModelsAvailableResponse(BaseModel):
    models: list[str]
    endpoints: list[dict]


class ModelAdditionalInstructionsResponse(BaseModel):
    model_id: str
    additional_instructions: str = ""


class ModelAdditionalInstructionsRequest(BaseModel):
    additional_instructions: str = ""


def get_settings_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[SettingsRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield SettingsRepository(connection)
    finally:
        connection.close()


def get_repository(
    settings: Annotated[Settings, Depends(get_settings)],
    project_id: str | None = None,
) -> Generator[TicketRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield TicketRepository(connection, project_id=project_id)
    finally:
        connection.close()


def get_requirement_repository(
    settings: Annotated[Settings, Depends(get_settings)],
    project_id: str | None = None,
) -> Generator[RequirementRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield RequirementRepository(connection, project_id=project_id)
    finally:
        connection.close()


def get_chat_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[ChatRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield ChatRepository(connection)
    finally:
        connection.close()


def get_run_event_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[RunEventRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield RunEventRepository(connection)
    finally:
        connection.close()


def get_identity_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[IdentityRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield IdentityRepository(connection)
    finally:
        connection.close()


def get_audit_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[AuditRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield AuditRepository(connection)
    finally:
        connection.close()


def get_runner_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[RunnerRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield RunnerRepository(connection)
    finally:
        connection.close()


def get_notification_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[NotificationRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield NotificationRepository(connection)
    finally:
        connection.close()


def get_eval_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[EvalRunRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield EvalRunRepository(connection)
    finally:
        connection.close()


def get_requirement_template_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[RequirementTemplateRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield RequirementTemplateRepository(connection)
    finally:
        connection.close()


def get_integration_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[IntegrationRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield IntegrationRepository(connection)
    finally:
        connection.close()


def get_attachment_storage() -> AttachmentStorage:
    return AttachmentStorage(PROJECT_ROOT / ".haao" / "attachments")


def get_project_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[ProjectRepository]:
    connection = connect(_sqlite_path(settings.database_url))
    try:
        yield ProjectRepository(connection)
    finally:
        connection.close()


def get_tech_lead_client(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> Generator[BaseCloudReasoner]:
    client = build_cloud_reasoner(settings, settings_repository)
    try:
        yield client
    finally:
        client.close()


def get_lmstudio_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Generator[LMStudioClient]:
    client = LMStudioClient(settings.lmstudio_base_url)
    try:
        yield client
    finally:
        client.close()


def get_requirement_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RequirementService:
    role_routing_store.bind_settings_repository(settings_repository)
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    return RequirementService(
        repository,
        requirement_repository,
        tech_lead,
        repo_root=repo_root,
        project_id=project.id,
        context_injector=ContextInjector(repo_root),
        settings_repository=settings_repository,
    )


# Which model drives the conversational agent: "cloud" (default) or "local".
CHAT_REASONER_MODE_SETTINGS_KEY = "chat_reasoner_mode"


def _local_chat_model(settings_repository: SettingsRepository) -> str | None:
    chain = local_model_fallback_chain(settings_repository)
    return chain[0] if chain else None


def _build_chat_reasoner(
    *,
    tech_lead: BaseCloudReasoner,
    ticket_repository: TicketRepository,
    settings: Settings,
    settings_repository: SettingsRepository,
):
    """Cloud by default; local only when explicitly selected.

    When local is selected we never silently fall back to cloud — a user who chose a
    local model (typically for privacy or cost) must not have their conversation
    quietly routed to a cloud model. If the local model is unavailable the reasoner
    raises a clear error at send time instead. Reading chat history stays available
    either way, since the reasoner is only invoked when sending a message.
    """
    mode = settings_repository.get_json(CHAT_REASONER_MODE_SETTINGS_KEY, "cloud")
    if mode == "local":
        endpoint = get_local_model_endpoints(settings_repository, settings)[0]
        client = LMStudioClient(endpoint.base_url, api_key=endpoint.api_key)
        return LocalChatReasoner(
            client, _local_chat_model(settings_repository) or "", ticket_repository
        )
    return CloudChatReasoner(tech_lead, ticket_repository)


def get_chat_service(
    chat_repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    requirement_service: Annotated[RequirementService, Depends(get_requirement_service)],
    repository: Annotated[TicketRepository, Depends(get_repository)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    tech_lead: Annotated[BaseCloudReasoner, Depends(get_tech_lead_client)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ChatService:
    return ChatService(
        repository=chat_repository,
        reasoner=_build_chat_reasoner(
            tech_lead=tech_lead,
            ticket_repository=repository,
            settings=settings,
            settings_repository=settings_repository,
        ),
        requirements=RequirementServiceGateway(
            requirement_service,
            ticket_repository=repository,
            requirement_repository=requirement_repository,
            tech_lead=tech_lead,
            project_repository=project_repository,
            settings_repository=settings_repository,
            chat_repository=chat_repository,
        ),
    )


def get_state_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
) -> TicketStateService:
    return TicketStateService(repository)


def get_manual_ticket_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ManualTicketService:
    role_routing_store.bind_settings_repository(settings_repository)
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    return ManualTicketService(
        repository,
        ContextInjector(repo_root),
        project_id=project.id,
        settings_repository=settings_repository,
    )


def get_execution_loop(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    state_service: Annotated[TicketStateService, Depends(get_state_service)],
    lmstudio: Annotated[LMStudioClient, Depends(get_lmstudio_client)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExecutionLoop:
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    role_routing_store.bind_settings_repository(settings_repository)
    return ExecutionLoop(
        repository,
        state_service,
        lmstudio,
        repo_root=repo_root,
        test_runner=TestRunner(
            cwd=repo_root,
            env=project.env,
            execution_policy=ExecutionPolicy(
                test_allow_network=project.test_allow_network,
                env_allowlist=tuple(project.env_allowlist),
                sandbox_mode=_effective_sandbox_mode(project.sandbox_mode, settings),
            ),
            setup_cmd=project.setup_cmd,
            cleanup_cmd=project.cleanup_cmd,
        ),
        settings_repository=settings_repository,
        requirement_repository=requirement_repository,
        max_output_tokens=settings.local_max_output_tokens,
        patch_mode_threshold_tokens=settings.local_patch_mode_threshold_tokens,
    )


def get_review_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    state_service: Annotated[TicketStateService, Depends(get_state_service)],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    requirement_repository: Annotated[RequirementRepository, Depends(get_requirement_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ReviewService:
    return ReviewService(
        repository,
        state_service,
        tech_lead,
        requirement_repository,
        settings_repository,
    )


def get_diff_review_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    state_service: Annotated[TicketStateService, Depends(get_state_service)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> DiffReviewService:
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    return DiffReviewService(
        repository,
        state_service,
        repo_root=repo_root,
        workspace_guard=GitWorkspaceGuard(repo_root),
    )


def get_git_ticket_flow(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> GitTicketFlow:
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    return GitTicketFlow(repo_root, workspace_guard=GitWorkspaceGuard(repo_root))


def get_pull_request_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    integrations: Annotated[IntegrationRepository, Depends(get_integration_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> PullRequestService:
    project = _resolve_project(project_repository, repository.project_id)
    return PullRequestService(
        repository=repository,
        integrations=integrations,
        run_events=run_events,
        repo_root=Path(project.path),
        base_branch=project.default_branch,
        workspace_guard=GitWorkspaceGuard(project.path),
    )


def get_escalation_service(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> EscalationService:
    return EscalationService(repository, tech_lead, settings_repository)


def get_auto_orchestrator(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    execution_loop: Annotated[ExecutionLoop, Depends(get_execution_loop)],
    review_service: Annotated[ReviewService, Depends(get_review_service)],
    escalation_service: Annotated[EscalationService, Depends(get_escalation_service)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> AutoOrchestrator:
    project = _resolve_project(project_repository, repository.project_id)
    repo_root = Path(project.path)
    return AutoOrchestrator(
        repository,
        execution_loop,
        review_service,
        escalation_service,
        repo_root=repo_root,
        workspace_guard=GitWorkspaceGuard(repo_root),
        allow_dirty_workspace=False,
    )


@router.post("/api/demo/seed", response_model=DemoSeedResponse)
@router.post("/demo/seed", response_model=DemoSeedResponse)
def seed_demo_project(
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> DemoSeedResponse:
    try:
        seeded = DemoSeedService(repository.connection).seed()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DemoSeedResponse(**seeded)


@router.post("/projects", response_model=ProjectResponse)
def create_project(
    request: ProjectCreateRequest,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectResponse:
    try:
        project = repository.create(
            name=request.name,
            path=request.path,
            default_branch=request.default_branch,
            project_id=request.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectResponse(project=project.to_dict())


@router.get("/projects", response_model=ProjectsResponse)
def list_projects(
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectsResponse:
    return ProjectsResponse(projects=[project.to_dict() for project in repository.list()])


@router.put("/projects/{project_id}/settings", response_model=ProjectResponse)
def update_project_settings(
    project_id: str,
    request: ProjectSettingsRequest,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectResponse:
    try:
        project = repository.update_settings(
            project_id,
            env=request.env,
            env_allowlist=request.env_allowlist,
            test_allow_network=request.test_allow_network,
            sandbox_mode=request.sandbox_mode,
            setup_cmd=request.setup_cmd,
            cleanup_cmd=request.cleanup_cmd,
            default_branch=request.default_branch,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectResponse(project=project.to_dict())


@router.delete("/projects/{project_id}", response_model=DeleteProjectResponse)
def delete_project(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> DeleteProjectResponse:
    try:
        repository.delete(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DeleteProjectResponse(deleted=True, project_id=project_id)


@router.get("/projects/{project_id}/conventions", response_model=ProjectConventionsResponse)
def get_project_conventions(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectConventionsResponse:
    project = _resolve_project(repository, project_id)
    repo_root = Path(project.path)
    return ProjectConventionsResponse(
        project_id=project.id,
        test_command=detect_test_command(repo_root),
        conventions=detect_conventions(repo_root),
    )


@router.get("/tickets", response_model=TicketsResponse)
def list_tickets(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    status: TicketStatus | None = None,
) -> TicketsResponse:
    return TicketsResponse(
        tickets=[ticket.to_dict() for ticket in repository.list(status=status)]
    )


@router.get("/api/tickets/graph", response_model=TicketGraphResponse)
@router.get("/tickets/graph", response_model=TicketGraphResponse)
def tickets_graph(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_id: str | None = None,
) -> TicketGraphResponse:
    scoped = repository.scoped(project_id) if project_id else repository
    effective_project_id = project_id or repository.project_id or "default"
    tickets = scoped.list(project_id=effective_project_id)
    active_leases = {ticket.id: ticket for ticket in scoped.active_leases(project_id=effective_project_id)}
    nodes = []
    edges = []
    by_id = {ticket.id: ticket for ticket in tickets}
    for ticket in tickets:
        dependencies = _ticket_depends_on(ticket)
        for dependency_id in dependencies:
            edges.append({"source": dependency_id, "target": ticket.id, "kind": "depends_on"})
        nodes.append(
            {
                "id": ticket.id,
                "status": ticket.status,
                "depends_on": dependencies,
                "target_files": ticket.task.target_files,
                "ready_state": _ticket_ready_state(ticket, by_id, active_leases),
                "leased": ticket.id in active_leases,
                "lease": _ticket_lease_payload(ticket),
            }
        )
    return TicketGraphResponse(
        project_id=effective_project_id,
        nodes=nodes,
        edges=edges,
    )


@router.post("/tickets", response_model=OperationResponse)
def create_manual_ticket(
    request: ManualTicketCreateRequest,
    service: Annotated[ManualTicketService, Depends(get_manual_ticket_service)],
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> OperationResponse:
    service = _body_project_manual_ticket_service(
        service,
        request.project_id,
        repository,
        project_repository,
        settings_repository,
    )
    try:
        ticket = service.create(
            ManualTicketCreatePayload(
                project_id=request.project_id,
                title=request.title,
                type=request.type,
                target_files=request.target_files,
                task_description=request.task_description,
                constraints=request.constraints,
                dod_tests=request.dod_tests,
                acceptance_criteria=request.acceptance_criteria,
                assigned_model=request.assigned_model,
            )
        )
    except DuplicateTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ManualTicketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperationResponse(ticket=ticket.to_dict())


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
) -> TicketResponse:
    try:
        ticket = repository.get(ticket_id)
    except AmbiguousTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketResponse(ticket=ticket.to_dict())


@router.delete("/tickets/{ticket_id}", response_model=DeleteTicketResponse)
def delete_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    force: bool = False,
) -> DeleteTicketResponse:
    try:
        repository.delete(ticket_id, force=force)
    except AmbiguousTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TicketDeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DeleteTicketResponse(deleted=True, ticket_id=ticket_id)


@router.get("/api/requirement-templates", response_model=RequirementTemplatesResponse)
@router.get("/requirement-templates", response_model=RequirementTemplatesResponse)
def list_requirement_templates(
    repository: Annotated[
        RequirementTemplateRepository,
        Depends(get_requirement_template_repository),
    ],
) -> RequirementTemplatesResponse:
    return RequirementTemplatesResponse(
        templates=[template.to_dict() for template in repository.list()]
    )


@router.post("/api/requirement-templates", response_model=RequirementTemplateResponse)
@router.post("/requirement-templates", response_model=RequirementTemplateResponse)
def upsert_requirement_template(
    request: RequirementTemplateRequest,
    repository: Annotated[
        RequirementTemplateRepository,
        Depends(get_requirement_template_repository),
    ],
) -> RequirementTemplateResponse:
    try:
        template = repository.upsert(
            template_id=request.id,
            title=request.title,
            prompt=request.prompt,
            scope_paths=request.scope_paths,
            constraints=request.constraints,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RequirementTemplateResponse(template=template.to_dict())


@router.delete("/api/requirement-templates/{template_id}", response_model=RequirementTemplateDeleteResponse)
@router.delete("/requirement-templates/{template_id}", response_model=RequirementTemplateDeleteResponse)
def delete_requirement_template(
    template_id: str,
    repository: Annotated[
        RequirementTemplateRepository,
        Depends(get_requirement_template_repository),
    ],
) -> RequirementTemplateDeleteResponse:
    try:
        repository.delete(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RequirementTemplateDeleteResponse(deleted=True, id=template_id)


@router.post("/requirements", response_model=RequirementResponse)
def submit_requirement(
    request: RequirementRequest,
    service: Annotated[RequirementService, Depends(get_requirement_service)],
    repository: Annotated[TicketRepository, Depends(get_repository)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RequirementResponse:
    service = _body_project_requirement_service(
        service,
        request.project_id,
        repository,
        requirement_repository,
        tech_lead,
        project_repository,
        settings_repository,
    )
    try:
        result = service.submit(request.requirement, request.repo_context)
    except DuplicateTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementResponse(tickets=[ticket.to_dict() for ticket in result.tickets])


@router.post("/requirements/decompose", response_model=RequirementPreviewResponse)
def decompose_requirement(
    request: RequirementDecomposeRequest,
    service: Annotated[RequirementService, Depends(get_requirement_service)],
    repository: Annotated[TicketRepository, Depends(get_repository)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RequirementPreviewResponse:
    service = _body_project_requirement_service(
        service,
        request.project_id,
        repository,
        requirement_repository,
        tech_lead,
        project_repository,
        settings_repository,
    )
    requirement = Requirement(
        id=service.next_requirement_id(),
        project_id=request.project_id or getattr(service, "project_id", None),
        prompt=request.prompt,
        repo=request.repo,
        branch=request.branch,
        scope_paths=request.scope_paths,
        constraints=request.constraints,
        priority=request.priority,
        intent=request.intent,
        scale=request.scale,
        granularity=request.granularity,
        allow_new_files=request.allow_new_files,
        test_command=request.test_command,
        attachments=request.attachments,
        acceptance_notes=request.acceptance_notes,
        status=RequirementStatus.DRAFT,
    )
    try:
        result = service.decompose_preview(requirement)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementPreviewResponse(
        requirement_id=result.requirement.id,
        requirement=result.requirement.to_dict(),
        proposed_tickets=[ticket.to_dict() for ticket in result.proposed_tickets],
    )


@router.post("/requirements/{requirement_id}/confirm", response_model=RequirementConfirmResponse)
def confirm_requirement(
    requirement_id: str,
    request: RequirementConfirmRequest,
    service: Annotated[RequirementService, Depends(get_requirement_service)],
    repository: Annotated[TicketRepository, Depends(get_repository)],
    requirement_repository: Annotated[
        RequirementRepository,
        Depends(get_requirement_repository),
    ],
    tech_lead: Annotated[ClaudeTechLeadClient, Depends(get_tech_lead_client)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RequirementConfirmResponse:
    service = _body_project_requirement_service(
        service,
        request.project_id,
        repository,
        requirement_repository,
        tech_lead,
        project_repository,
        settings_repository,
    )
    try:
        result = service.confirm(requirement_id, request.tickets)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateTicketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementConfirmResponse(
        requirement=result.requirement.to_dict(),
        tickets=[ticket.to_dict() for ticket in result.tickets],
    )


@router.post("/requirements/{requirement_id}/discard", response_model=RequirementDetailResponse)
def discard_requirement(
    requirement_id: str,
    service: Annotated[RequirementService, Depends(get_requirement_service)],
) -> RequirementDetailResponse:
    try:
        requirement = service.discard(requirement_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RequirementDetailResponse(requirement=requirement.to_dict())


@router.get("/requirements", response_model=RequirementListResponse)
def list_requirements(
    service: Annotated[RequirementService, Depends(get_requirement_service)],
) -> RequirementListResponse:
    return RequirementListResponse(
        requirements=[
            requirement.to_dict()
            for requirement in service.list_requirements()
        ]
    )


@router.get("/requirements/{requirement_id}", response_model=RequirementDetailResponse)
def get_requirement(
    requirement_id: str,
    service: Annotated[RequirementService, Depends(get_requirement_service)],
) -> RequirementDetailResponse:
    requirement = service.get_requirement(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return RequirementDetailResponse(requirement=requirement.to_dict())


@router.get("/api/requirements/{requirement_id}/summary", response_model=RequirementSummaryResponse)
@router.get("/requirements/{requirement_id}/summary", response_model=RequirementSummaryResponse)
def get_requirement_summary(
    requirement_id: str,
    repository: Annotated[RequirementRepository, Depends(get_requirement_repository)],
    project_id: str | None = None,
) -> RequirementSummaryResponse:
    try:
        summary = build_requirement_summary(
            repository.connection,
            requirement_id=requirement_id,
            project_id=project_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RequirementSummaryResponse(summary=summary)


@router.post("/api/chat/attachments", response_model=ChatAttachmentResponse)
@router.post("/chat/attachments", response_model=ChatAttachmentResponse)
async def upload_chat_attachment(
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[AttachmentStorage, Depends(get_attachment_storage)],
    project_id: str = Form("default"),
    file: UploadFile = File(...),
) -> ChatAttachmentResponse:
    content = await file.read()
    try:
        upload = storage.store(
            project_id=project_id,
            filename=file.filename or "attachment",
            mime=file.content_type,
            content=content,
        )
        attachment = repository.create_attachment(project_id=project_id, upload=upload)
    except AttachmentStorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _chat_attachment_response(attachment)


@router.get("/api/chat/attachments/{attachment_id}/content")
@router.get("/chat/attachments/{attachment_id}/content")
def get_chat_attachment_content(
    attachment_id: str,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    project_id: str = "default",
) -> FileResponse:
    attachments = repository.attachments_by_ids(project_id, [attachment_id])
    if not attachments:
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment = attachments[0]
    path = Path(attachment.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing")
    return FileResponse(path, media_type=attachment.mime, filename=attachment.filename)


@router.post("/api/chat/messages", response_model=ChatTurnResponse)
@router.post("/chat/messages", response_model=ChatTurnResponse)
def create_chat_message(
    request: ChatMessageCreateRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatTurnResponse:
    try:
        try:
            result = service.handle_user_message(
                request.project_id,
                request.text,
                attachment_ids=request.attachment_ids,
            )
        except TypeError:
            if request.attachment_ids:
                raise
            result = service.handle_user_message(request.project_id, request.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatTurnResponse(
        messages=[
            _chat_message_response(message, repository)
            for message in result.messages
        ],
        filed_requirement_ids=result.filed_requirement_ids,
    )


@router.get("/api/chat/messages", response_model=ChatMessagesResponse)
@router.get("/chat/messages", response_model=ChatMessagesResponse)
def list_chat_messages(
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    project_id: str,
    segment_id: str | None = None,
    after: str | None = None,
    limit: int | None = None,
) -> ChatMessagesResponse:
    return ChatMessagesResponse(
        messages=[
            _chat_message_response(message, repository)
            for message in repository.list_messages(
                project_id,
                segment_id=segment_id,
                after=after,
                limit=limit,
            )
        ]
    )


@router.post("/api/chat/segments", response_model=ChatSegmentResponse)
@router.post("/chat/segments", response_model=ChatSegmentResponse)
def create_chat_segment(
    request: ChatSegmentCreateRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatSegmentResponse:
    try:
        segment = repository.create_segment(project_id=request.project_id, title=request.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatSegmentResponse(**segment.to_dict())


@router.get("/api/chat/segments", response_model=ChatSegmentsResponse)
@router.get("/chat/segments", response_model=ChatSegmentsResponse)
def list_chat_segments(
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    project_id: str,
) -> ChatSegmentsResponse:
    return ChatSegmentsResponse(
        segments=[
            ChatSegmentResponse(**segment.to_dict())
            for segment in repository.list_segments(project_id)
        ]
    )


@router.get("/api/run-events", response_model=RunEventsResponse)
@router.get("/run-events", response_model=RunEventsResponse)
def list_run_events(
    repository: Annotated[RunEventRepository, Depends(get_run_event_repository)],
    project_id: str,
    after: int | None = None,
    limit: int | None = None,
    ticket_id: str | None = None,
) -> RunEventsResponse:
    return RunEventsResponse(
        events=[
            event.to_dict()
            for event in repository.list_run_events(
                project_id,
                after=after,
                limit=limit,
                ticket_id=ticket_id,
            )
        ]
    )


@router.get("/api/audit", response_model=AuditEventsResponse)
@router.get("/audit", response_model=AuditEventsResponse)
def list_audit_events(
    repository: Annotated[AuditRepository, Depends(get_audit_repository)],
    workspace: str = "default",
    cursor: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> AuditEventsResponse:
    events = repository.list(workspace_id=workspace, cursor=cursor, limit=limit)
    return AuditEventsResponse(
        events=[event.to_dict() for event in events],
        next_cursor=events[-1].id if events else cursor,
    )


@router.post("/api/memberships", response_model=MembershipResponse)
@router.post("/memberships", response_model=MembershipResponse)
def upsert_membership(
    request: MembershipUpsertRequest,
    repository: Annotated[IdentityRepository, Depends(get_identity_repository)],
) -> MembershipResponse:
    repository.create_user(
        user_id=request.user_id,
        email=request.email,
        display_name=request.display_name,
    )
    membership = repository.set_membership(
        user_id=request.user_id,
        workspace_id=request.workspace_id,
        role=request.role,
    )
    return MembershipResponse(membership=membership.to_dict())


@router.post("/api/runner/register", response_model=RunnerRegisterResponse)
@router.post("/runner/register", response_model=RunnerRegisterResponse)
def register_runner(
    request: RunnerRegisterRequest,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
    audit: Annotated[AuditRepository, Depends(get_audit_repository)],
) -> RunnerRegisterResponse:
    issued = repository.issue_token(workspace_id=request.workspace_id, label=request.label)
    audit.append(
        actor_id="control-plane",
        workspace_id=request.workspace_id,
        action="runner.token.issue",
        target=issued.runner.id,
        payload={"label": request.label},
    )
    return RunnerRegisterResponse(runner=issued.runner.to_dict(), token=issued.token)


@router.post("/api/runner/revoke/{runner_id}", response_model=RunnerHeartbeatResponse)
@router.post("/runner/revoke/{runner_id}", response_model=RunnerHeartbeatResponse)
def revoke_runner(
    runner_id: str,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
    audit: Annotated[AuditRepository, Depends(get_audit_repository)],
) -> RunnerHeartbeatResponse:
    try:
        runner = repository.revoke(runner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.append(
        actor_id="control-plane",
        workspace_id=runner.workspace_id,
        action="runner.token.revoke",
        target=runner.id,
    )
    return RunnerHeartbeatResponse(runner=runner.to_dict())


@router.post("/api/runner/jobs", response_model=RunnerJobResponse)
@router.post("/runner/jobs", response_model=RunnerJobResponse)
def enqueue_runner_job(
    request: RunnerJobCreateRequest,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
) -> RunnerJobResponse:
    job = repository.enqueue_job(
        workspace_id=request.workspace_id,
        ticket_id=request.ticket_id,
        payload=request.payload,
    )
    return RunnerJobResponse(job=job.to_dict())


@router.post("/api/runner/heartbeat", response_model=RunnerHeartbeatResponse)
@router.post("/runner/heartbeat", response_model=RunnerHeartbeatResponse)
def runner_heartbeat(
    request: Request,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
) -> RunnerHeartbeatResponse:
    runner = _require_runner(request, repository)
    refreshed = repository.heartbeat(_runner_token_from_request(request))
    return RunnerHeartbeatResponse(runner=(refreshed or runner).to_dict())


@router.post("/api/runner/lease", response_model=RunnerJobResponse)
@router.post("/runner/lease", response_model=RunnerJobResponse)
def lease_runner_job(
    request_body: RunnerLeaseRequest,
    request: Request,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
) -> RunnerJobResponse:
    runner = _require_runner(request, repository)
    job = repository.lease_next_job(runner=runner, ttl_sec=request_body.ttl_sec)
    return RunnerJobResponse(job=job.to_dict() if job else None)


@router.post("/api/runner/events", response_model=RunnerEventsResponse)
@router.post("/runner/events", response_model=RunnerEventsResponse)
def ingest_runner_events(
    request_body: RunnerEventsRequest,
    request: Request,
    runner_repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
) -> RunnerEventsResponse:
    runner = _require_runner(request, runner_repository)
    accepted = 0
    for event in request_body.events:
        if not isinstance(event, dict):
            continue
        run_events.append_run_event(
            project_id=str(event.get("project_id") or runner.workspace_id),
            requirement_id=_string_or_none(event.get("requirement_id")),
            ticket_id=_string_or_none(event.get("ticket_id")),
            run_id=_string_or_none(event.get("run_id")),
            event_type=str(event.get("event_type") or "report"),
            model_id=_string_or_none(event.get("model_id")),
            payload=event.get("payload") if isinstance(event.get("payload"), dict) else {},
        )
        accepted += 1
    return RunnerEventsResponse(accepted=accepted)


@router.post("/api/runner/jobs/{job_id}/complete", response_model=RunnerJobResponse)
@router.post("/runner/jobs/{job_id}/complete", response_model=RunnerJobResponse)
def complete_runner_job(
    job_id: str,
    request_body: RunnerCompleteRequest,
    request: Request,
    repository: Annotated[RunnerRepository, Depends(get_runner_repository)],
) -> RunnerJobResponse:
    runner = _require_runner(request, repository)
    try:
        job = repository.complete_job(
            job_id=job_id,
            runner=runner,
            result=request_body.result,
            status=request_body.status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RunnerJobResponse(job=job.to_dict())


@router.get("/api/insights", response_model=InsightsResponse)
@router.get("/insights", response_model=InsightsResponse)
def get_insights(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_id: str,
    range_name: Annotated[Literal["7d", "30d", "all"], Query(alias="range")] = "30d",
) -> InsightsResponse:
    return InsightsResponse(
        **InsightsService(repository.connection).aggregate(
            project_id=project_id,
            range_name=range_name,
        )
    )


@router.get("/api/decisions", response_model=DecisionsResponse)
@router.get("/decisions", response_model=DecisionsResponse)
def get_decisions(
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_id: str = "default",
) -> DecisionsResponse:
    return DecisionsResponse(**build_decision_center(repository.connection, project_id=project_id))


@router.get("/api/tickets/{ticket_id}/signals", response_model=TicketSignalsResponse)
@router.get("/tickets/{ticket_id}/signals", response_model=TicketSignalsResponse)
def get_ticket_signals(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
) -> TicketSignalsResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketSignalsResponse(signals=build_ticket_signals(ticket))


@router.get("/api/requirements/{requirement_id}/signals", response_model=RequirementSignalsResponse)
@router.get("/requirements/{requirement_id}/signals", response_model=RequirementSignalsResponse)
def get_requirement_signals(
    requirement_id: str,
    repository: Annotated[RequirementRepository, Depends(get_requirement_repository)],
    project_id: str | None = None,
) -> RequirementSignalsResponse:
    requirement = repository.get(requirement_id, project_id=project_id)
    if requirement is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return RequirementSignalsResponse(signals=build_requirement_signals(requirement))


@router.get("/api/tickets/{ticket_id}/acceptance-summary", response_model=AcceptanceSummaryResponse)
@router.get("/tickets/{ticket_id}/acceptance-summary", response_model=AcceptanceSummaryResponse)
def get_acceptance_summary(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
) -> AcceptanceSummaryResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return AcceptanceSummaryResponse(summary=build_acceptance_summary(ticket))


@router.get("/api/evals/task-sets", response_model=EvalTaskSetsResponse)
@router.get("/evals/task-sets", response_model=EvalTaskSetsResponse)
def list_eval_task_sets(
    repository: Annotated[EvalRunRepository, Depends(get_eval_repository)],
) -> EvalTaskSetsResponse:
    try:
        service = EvalService(repository)
        return EvalTaskSetsResponse(task_sets=[task_set.to_dict() for task_set in service.list_task_sets()])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/evals", response_model=EvalRunsResponse)
@router.get("/evals", response_model=EvalRunsResponse)
def list_eval_runs(
    repository: Annotated[EvalRunRepository, Depends(get_eval_repository)],
    model_id: str | None = Query(default=None),
    task_set_id: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> EvalRunsResponse:
    return EvalRunsResponse(
        eval_runs=[
            run.to_dict()
            for run in repository.list(
                model_id=model_id,
                task_set_id=task_set_id,
                limit=limit,
            )
        ]
    )


@router.post("/api/evals/run", response_model=EvalRunResponse)
@router.post("/evals/run", response_model=EvalRunResponse)
def start_eval_run(
    request: EvalRunRequest,
    background_tasks: BackgroundTasks,
    repository: Annotated[EvalRunRepository, Depends(get_eval_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EvalRunResponse:
    try:
        service = EvalService(repository)
        run = service.start_run(
            model_id=request.model_id,
            task_set_id=request.task_set_id,
            trials=request.trials,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(
        _run_eval_background,
        settings.database_url,
        run.id,
        settings,
    )
    return EvalRunResponse(eval_run=run.to_dict())


@router.get("/api/notifications", response_model=NotificationsResponse)
@router.get("/notifications", response_model=NotificationsResponse)
def list_notifications(
    repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
    project_id: str | None = None,
    unread_only: bool = False,
    limit: int | None = None,
) -> NotificationsResponse:
    return NotificationsResponse(
        notifications=[
            notification.to_dict()
            for notification in repository.list(
                project_id=project_id,
                unread_only=unread_only,
                limit=limit,
            )
        ],
        unread_count=repository.unread_counts(),
    )


@router.post("/api/notifications/{notification_id}/read", response_model=NotificationReadResponse)
@router.post("/notifications/{notification_id}/read", response_model=NotificationReadResponse)
def mark_notification_read(
    notification_id: int,
    repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
) -> NotificationReadResponse:
    try:
        notification = repository.mark_read(notification_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NotificationReadResponse(
        notification=notification.to_dict(),
        unread_count=repository.unread_counts(),
    )


@router.post("/api/notifications/read-all", response_model=NotificationReadAllResponse)
@router.post("/notifications/read-all", response_model=NotificationReadAllResponse)
def mark_all_notifications_read(
    repository: Annotated[NotificationRepository, Depends(get_notification_repository)],
    project_id: str | None = None,
) -> NotificationReadAllResponse:
    updated = repository.mark_all_read(project_id=project_id)
    return NotificationReadAllResponse(
        updated=updated,
        unread_count=repository.unread_counts(),
    )


@router.post("/tickets/{ticket_id}/move", response_model=OperationResponse)
def move_ticket(
    ticket_id: str,
    request: MoveRequest,
    service: Annotated[TicketStateService, Depends(get_state_service)],
) -> OperationResponse:
    try:
        result = service.move(ticket_id, request.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/tickets/{ticket_id}/approve", response_model=OperationResponse)
def approve_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    service: Annotated[TicketStateService, Depends(get_state_service)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket_json = ticket.to_dict()
    metadata = ticket_json.setdefault("metadata", {})
    metadata["needs_approval"] = False
    metadata["approved_by"] = "product-owner"
    metadata["approved_at"] = _now()
    role_routing_store.bind_settings_repository(settings_repository)
    # Preserve the model chosen for this ticket; local_execution_model keeps a
    # valid local model and only falls back to dev_team routing if it isn't one.
    ticket_json["execution"]["assigned_model"] = local_execution_model(
        ticket.execution.assigned_model,
        repository=settings_repository,
    )
    repository.save(type(ticket).from_dict(ticket_json))
    try:
        result = service.move(ticket_id, TicketStatus.READY)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.append_log(ticket_id, "Product Owner approved backlog item; ready for auto-dispatch")
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/tickets/{ticket_id}/accept", response_model=OperationResponse)
def accept_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    service: Annotated[TicketStateService, Depends(get_state_service)],
    pr_service: Annotated[PullRequestService, Depends(get_pull_request_service)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if pr_service.has_pr_integration():
        try:
            pr_service.open_or_update_pr(ticket_id)
        except AcceptanceGateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except DirtyWorkspaceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PullRequestFlowError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ticket = repository.get(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
    ticket_json = ticket.to_dict()
    metadata = ticket_json.setdefault("metadata", {})
    metadata["accepted_by"] = "product-owner"
    metadata["accepted_at"] = _now()
    repository.save(type(ticket).from_dict(ticket_json))
    try:
        result = service.move(ticket_id, TicketStatus.DONE)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.append_log(ticket_id, "Product Owner accepted ticket")
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/api/tickets/{ticket_id}/pr", response_model=PullRequestResponse)
@router.post("/tickets/{ticket_id}/pr", response_model=PullRequestResponse)
def open_ticket_pr(
    ticket_id: str,
    service: Annotated[PullRequestService, Depends(get_pull_request_service)],
) -> PullRequestResponse:
    try:
        result = service.open_or_update_pr(ticket_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AcceptanceGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DirtyWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissingIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PullRequestFlowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PullRequestResponse(pr_url=result.pr_url, status=result.status)


@router.post("/tickets/{ticket_id}/reject", response_model=OperationResponse)
def reject_ticket(
    ticket_id: str,
    request: RejectRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    service: Annotated[TicketStateService, Depends(get_state_service)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket_json = ticket.to_dict()
    metadata = ticket_json.setdefault("metadata", {})
    metadata["product_rejected_by"] = "product-owner"
    metadata["product_rejected_at"] = _now()
    metadata["product_rejection_feedback"] = request.feedback
    metadata["needs_approval"] = True
    repository.save(type(ticket).from_dict(ticket_json))
    try:
        result = service.move(ticket_id, TicketStatus.BACKLOG)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.append_log(
        ticket_id,
        f"Product Owner rejected ticket: {request.feedback}",
        level="warn",
    )
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/api/tickets/{ticket_id}/split", response_model=SplitTicketResponse)
@router.post("/tickets/{ticket_id}/split", response_model=SplitTicketResponse)
def split_ticket(
    ticket_id: str,
    request: SplitTicketRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
) -> SplitTicketResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status in {"in_progress", "testing"}:
        raise HTTPException(status_code=409, detail="Live tickets must be cancelled before split")
    if ticket.status in {TicketStatus.DONE, TicketStatus.ABANDONED, TicketStatus.SPLIT}:
        raise HTTPException(status_code=409, detail="Terminal tickets cannot be split")
    project_id = _ticket_project_id(ticket)
    child_payloads = _split_child_ticket_payloads(ticket, request.feedback, repository)
    children = [repository.create(Ticket.from_dict(payload), project_id=project_id) for payload in child_payloads]
    ticket_json = ticket.to_dict()
    ticket_json["status"] = TicketStatus.SPLIT.value
    metadata = ticket_json.setdefault("metadata", {})
    metadata["needs_approval"] = False
    metadata["split_requested"] = True
    metadata["split_feedback"] = request.feedback
    metadata["split_requested_by"] = "product-owner"
    metadata["split_requested_at"] = _now()
    metadata["previous_status_before_split"] = str(ticket.status)
    metadata["child_ticket_ids"] = [child.id for child in children]
    if ticket.result and ticket.result.diff:
        metadata["previous_rejected_diff"] = ticket.result.diff
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(
        ticket_id,
        f"Ticket split by Product Owner into {', '.join(child.id for child in children)}: {request.feedback}",
        level="warn",
    )
    run_events.append_run_event(
        project_id=_ticket_project_id(updated),
        requirement_id=_ticket_requirement_id(updated),
        ticket_id=updated.id,
        run_id=_ticket_run_id(updated),
        event_type="report",
        model_id=updated.execution.assigned_model,
        payload={
            "report_kind": "needs_you",
            "action": "split",
            "reason": request.feedback,
            "from_status": str(ticket.status),
            "to_status": TicketStatus.SPLIT.value,
            "child_ticket_ids": [child.id for child in children],
        },
    )
    for child in children:
        repository.append_log(child.id, f"Created by split from {ticket.id}: {request.feedback}")
        run_events.append_run_event(
            project_id=_ticket_project_id(child),
            requirement_id=_ticket_requirement_id(child),
            ticket_id=child.id,
            run_id=None,
            event_type="report",
            model_id=child.execution.assigned_model,
            payload={
                "report_kind": "needs_you",
                "action": "split_from",
                "parent_ticket_id": ticket.id,
                "reason": request.feedback,
            },
        )
    return SplitTicketResponse(
        parent_id=updated.id,
        child_ticket_ids=[child.id for child in children],
        ticket=updated.to_dict(),
        children=[child.to_dict() for child in children],
    )


@router.post("/api/tickets/{ticket_id}/abandon", response_model=OperationResponse)
@router.post("/tickets/{ticket_id}/abandon", response_model=OperationResponse)
def abandon_ticket(
    ticket_id: str,
    request: AbandonTicketRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status in {"in_progress", "testing"}:
        raise HTTPException(status_code=409, detail="Live tickets must be cancelled before abandon")
    if ticket.status in {TicketStatus.DONE, TicketStatus.ABANDONED, TicketStatus.SPLIT}:
        raise HTTPException(status_code=409, detail="Terminal tickets cannot be abandoned")
    ticket_json = ticket.to_dict()
    ticket_json["status"] = TicketStatus.ABANDONED.value
    metadata = ticket_json.setdefault("metadata", {})
    metadata["abandoned"] = True
    metadata["abandoned_by"] = "product-owner"
    metadata["abandoned_at"] = _now()
    metadata["abandon_reason"] = request.reason
    metadata["previous_status_before_abandon"] = str(ticket.status)
    metadata["needs_approval"] = False
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(ticket_id, f"Product Owner abandoned ticket: {request.reason}", level="warn")
    run_events.append_run_event(
        project_id=_ticket_project_id(updated),
        requirement_id=_ticket_requirement_id(updated),
        ticket_id=updated.id,
        run_id=_ticket_run_id(updated),
        event_type="report",
        model_id=updated.execution.assigned_model,
        payload={
            "report_kind": "done",
            "action": "abandon",
            "reason": request.reason,
            "from_status": str(ticket.status),
            "to_status": TicketStatus.ABANDONED.value,
        },
    )
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/assign_model", response_model=OperationResponse)
def assign_model(
    ticket_id: str,
    request: AssignModelRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    role_routing_store.bind_settings_repository(settings_repository)
    ticket_json = ticket.to_dict()
    ticket_json["execution"]["assigned_model"] = local_execution_model(
        request.model,
        repository=settings_repository,
    )
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(ticket_id, f"Assigned model: {updated.execution.assigned_model}")
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/retry", response_model=OperationResponse)
def retry_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status not in {"blocked", "backlog"}:
        raise HTTPException(status_code=409, detail="Only blocked or backlog tickets can be retried manually")
    ticket_json = ticket.to_dict()
    ticket_json["status"] = "ready"
    ticket_json["execution"]["attempts"] = 0
    metadata = ticket_json.setdefault("metadata", {})
    if ticket.result and ticket.result.diff:
        metadata["previous_rejected_diff"] = ticket.result.diff
    if ticket.audit and ticket.audit.verdict == "rejected" and ticket.audit.feedback:
        metadata["previous_review_feedback"] = ticket.audit.feedback
    ticket_json["result"] = {"outcome": "pending"}
    ticket_json["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
    metadata["manual_retry"] = True
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(ticket_id, "Manual retry requested")
    run_events.append_run_event(
        project_id=_ticket_project_id(updated),
        requirement_id=_ticket_requirement_id(updated),
        ticket_id=updated.id,
        run_id=_ticket_run_id(updated),
        event_type="retry",
        model_id=updated.execution.assigned_model,
        payload={
            "reason": "manual_retry",
            "attempt": 0,
            "retry_budget": updated.execution.retry_budget,
        },
    )
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/escalate", response_model=OperationResponse)
def escalate_ticket(
    ticket_id: str,
    request: EscalateRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    run_events: Annotated[RunEventRepository, Depends(get_run_event_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket_json = ticket.to_dict()
    ticket_json["status"] = "blocked"
    metadata = ticket_json.setdefault("metadata", {})
    metadata["escalated_to"] = request.escalated_to or ticket.execution.escalate_to
    metadata["escalation_reason"] = request.reason
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(ticket_id, f"Manual escalation: {request.reason}", level="warn")
    run_events.append_run_event(
        project_id=_ticket_project_id(updated),
        requirement_id=_ticket_requirement_id(updated),
        ticket_id=updated.id,
        run_id=_ticket_run_id(updated),
        event_type="escalation",
        model_id=updated.execution.assigned_model,
        payload={
            "reason": request.reason,
            "escalated_to": metadata["escalated_to"],
            "manual": True,
        },
    )
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/execute", response_model=OperationResponse)
def execute_ticket(
    ticket_id: str,
    loop: Annotated[ExecutionLoop, Depends(get_execution_loop)],
) -> OperationResponse:
    try:
        result = loop.run_ticket(ticket_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/tickets/{ticket_id}/cancel", response_model=OperationResponse)
def cancel_ticket(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    registry_key = execution_key(repository.project_id, ticket_id)
    cancelled = execution_registry.request_cancel(registry_key)
    worktree = execution_registry.get_worktree(registry_key)
    if worktree is not None:
        project = _resolve_project(project_repository, repository.project_id)
        GitWorkspaceGuard(project.path).remove_worktree(worktree)

    if cancelled:
        refreshed = repository.get(ticket_id)
        if refreshed is not None:
            return OperationResponse(ticket=refreshed.to_dict())

    if ticket.status not in {"in_progress", "testing"}:
        raise HTTPException(
            status_code=409,
            detail="Ticket is not currently executing",
        )

    ticket_json = ticket.to_dict()
    ticket_json["status"] = "ready"
    ticket_json["execution"]["attempts"] = 0
    ticket_json["result"] = {"outcome": "pending"}
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(ticket_id, "Execution cancelled by user", level="warn")
    return OperationResponse(ticket=updated.to_dict())


@router.patch("/tickets/{ticket_id}", response_model=OperationResponse)
def update_ticket(
    ticket_id: str,
    request: UpdateTicketRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if execution_registry.is_registered(execution_key(repository.project_id, ticket_id)):
        raise HTTPException(status_code=409, detail="Cannot edit a ticket while it is executing")

    ticket_json = ticket.to_dict()
    if request.task_description is not None:
        ticket_json["task"]["description"] = request.task_description
    if request.task_target_files is not None:
        if not request.task_target_files:
            raise HTTPException(status_code=400, detail="task_target_files cannot be empty")
        ticket_json["task"]["target_files"] = request.task_target_files
    if request.dod_tests is not None:
        if not request.dod_tests:
            raise HTTPException(status_code=400, detail="dod_tests cannot be empty")
        ticket_json["definition_of_done"]["tests"] = [
            {"command": command, "expect": "pass", "timeout_sec": 120}
            for command in request.dod_tests
        ]
    if request.assigned_model is not None:
        role_routing_store.bind_settings_repository(settings_repository)
        ticket_json["execution"]["assigned_model"] = local_execution_model(
            request.assigned_model,
            repository=settings_repository,
        )

    if request.rerun:
        ticket_json["status"] = "ready"
        ticket_json["execution"]["attempts"] = 0
        ticket_json["result"] = {"outcome": "pending"}
        ticket_json["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}

    try:
        updated = repository.save(type(ticket).from_dict(ticket_json))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.rerun:
        repository.append_log(ticket_id, "Ticket updated and reset for rerun")
    else:
        repository.append_log(ticket_id, "Ticket fields updated")
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/diff/approve", response_model=OperationResponse)
def approve_diff(
    ticket_id: str,
    service: Annotated[DiffReviewService, Depends(get_diff_review_service)],
) -> OperationResponse:
    try:
        result = service.approve_diff(ticket_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/tickets/{ticket_id}/diff/reject", response_model=OperationResponse)
def reject_diff(
    ticket_id: str,
    request: DiffRejectRequest,
    service: Annotated[DiffReviewService, Depends(get_diff_review_service)],
) -> OperationResponse:
    try:
        result = service.reject_diff(ticket_id, request.feedback)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/tickets/{ticket_id}/merge", response_model=OperationResponse)
def merge_ticket_branch(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    git_flow: Annotated[GitTicketFlow, Depends(get_git_ticket_flow)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        merge = git_flow.merge_ticket_branch(ticket)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ticket_json = ticket.to_dict()
    metadata = ticket_json.setdefault("metadata", {})
    metadata["git_merged_at"] = now_iso()
    metadata["git_merged_to"] = merge.base_branch
    metadata["git_merge_commit"] = merge.merge_commit
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(
        ticket_id,
        f"Merged {merge.branch} into {merge.base_branch} at {merge.merge_commit[:12]}",
    )
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/revert", response_model=OperationResponse)
def revert_ticket_merge(
    ticket_id: str,
    repository: Annotated[TicketRepository, Depends(get_repository)],
    git_flow: Annotated[GitTicketFlow, Depends(get_git_ticket_flow)],
) -> OperationResponse:
    ticket = repository.get(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        revert = git_flow.revert_ticket_merge(ticket)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ticket_json = ticket.to_dict()
    ticket_json["status"] = TicketStatus.AWAITING_ACCEPTANCE.value
    metadata = ticket_json.setdefault("metadata", {})
    metadata["git_reverted_at"] = now_iso()
    metadata["git_reverted_commit"] = revert.reverted_commit
    metadata["git_revert_commit"] = revert.revert_commit
    metadata["git_reverted_on"] = revert.base_branch
    updated = repository.save(type(ticket).from_dict(ticket_json))
    repository.append_log(
        ticket_id,
        f"Reverted {revert.reverted_commit[:12]} on {revert.base_branch} at {revert.revert_commit[:12]}",
        level="warn",
    )
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/review", response_model=OperationResponse)
def review_ticket(
    ticket_id: str,
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> OperationResponse:
    try:
        result = service.review_ticket(ticket_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OperationResponse(ticket=result.ticket.to_dict())


@router.post("/orchestrator/run-once", response_model=OrchestratorRunResponse)
def run_orchestrator_once(
    orchestrator: Annotated[AutoOrchestrator, Depends(get_auto_orchestrator)],
) -> OrchestratorRunResponse:
    result = orchestrator.run_once()
    return OrchestratorRunResponse(results=[result.__dict__])


@router.post("/orchestrator/run-until-idle", response_model=OrchestratorRunResponse)
def run_orchestrator_until_idle(
    request: OrchestratorRunRequest,
    orchestrator: Annotated[AutoOrchestrator, Depends(get_auto_orchestrator)],
) -> OrchestratorRunResponse:
    orchestrator.allow_dirty_workspace = request.allow_dirty_workspace
    results = orchestrator.run_until_idle(max_cycles=request.max_cycles)
    return OrchestratorRunResponse(results=[result.__dict__ for result in results])


@router.get("/orchestrator/worker/status", response_model=AutoWorkerResponse)
def get_auto_worker_status() -> AutoWorkerResponse:
    return AutoWorkerResponse(**auto_worker.snapshot().__dict__)


@router.post("/orchestrator/worker/start", response_model=AutoWorkerResponse)
async def start_auto_worker(
    request: AutoWorkerRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> AutoWorkerResponse:
    project = _resolve_project(project_repository, request.project_id)
    snapshot = await auto_worker.start(
        settings=settings,
        project_id=project.id,
        repo_root=Path(project.path),
        database_root=PROJECT_ROOT,
        env=project.env,
        env_allowlist=project.env_allowlist,
        test_allow_network=project.test_allow_network,
        sandbox_mode=_effective_sandbox_mode(project.sandbox_mode, settings),
        setup_cmd=project.setup_cmd,
        cleanup_cmd=project.cleanup_cmd,
        interval_sec=request.interval_sec,
        max_cycles_per_tick=request.max_cycles_per_tick,
        max_workers=request.max_workers,
        allow_dirty_workspace=request.allow_dirty_workspace,
    )
    return AutoWorkerResponse(**snapshot.__dict__)


@router.post("/orchestrator/worker/stop", response_model=AutoWorkerResponse)
async def stop_auto_worker() -> AutoWorkerResponse:
    snapshot = await auto_worker.stop()
    return AutoWorkerResponse(**snapshot.__dict__)


@router.get("/models/local/endpoints", response_model=LocalModelEndpointsResponse)
def list_local_model_endpoints(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> LocalModelEndpointsResponse:
    endpoints = get_local_model_endpoints(settings_repository, settings)
    return LocalModelEndpointsResponse(
        endpoints=[endpoint.to_public_dict() for endpoint in endpoints],
    )


@router.put("/models/local/endpoints", response_model=LocalModelEndpointsResponse)
def update_local_model_endpoints(
    request: LocalModelEndpointsRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> LocalModelEndpointsResponse:
    endpoints = [
        LocalModelEndpoint(
            id=item.id.strip(),
            label=item.label.strip(),
            base_url=item.base_url.strip().rstrip("/"),
            api_key=item.api_key,
        )
        for item in request.endpoints
    ]
    if any(not endpoint.base_url for endpoint in endpoints):
        raise HTTPException(status_code=400, detail="Endpoint base_url cannot be empty")
    saved = set_local_model_endpoints(settings_repository, endpoints)
    return LocalModelEndpointsResponse(
        endpoints=[endpoint.to_public_dict() for endpoint in saved],
    )


@router.get("/models/local/available", response_model=LocalModelsAvailableResponse)
def list_available_local_models(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> LocalModelsAvailableResponse:
    endpoints = get_local_model_endpoints(settings_repository, settings)
    endpoint_results = discover_local_models(endpoints)
    models = sorted({model for result in endpoint_results for model in result.models})
    cache_local_models(settings_repository, models)
    return LocalModelsAvailableResponse(
        models=models,
        endpoints=[result.to_dict() for result in endpoint_results],
    )


@router.get(
    "/models/{model_id:path}/additional_instructions",
    response_model=ModelAdditionalInstructionsResponse,
)
def get_model_additional_instructions(
    model_id: str,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ModelAdditionalInstructionsResponse:
    normalized = normalize_model_settings_id(model_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="model_id cannot be empty")
    return ModelAdditionalInstructionsResponse(
        model_id=normalized,
        additional_instructions=settings_repository.get_model_addon(normalized),
    )


@router.put(
    "/models/{model_id:path}/additional_instructions",
    response_model=ModelAdditionalInstructionsResponse,
)
def update_model_additional_instructions(
    model_id: str,
    request: ModelAdditionalInstructionsRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ModelAdditionalInstructionsResponse:
    normalized = normalize_model_settings_id(model_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="model_id cannot be empty")
    try:
        saved = settings_repository.set_model_addon(normalized, request.additional_instructions)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelAdditionalInstructionsResponse(
        model_id=normalized,
        additional_instructions=saved,
    )


@router.get("/config/role-routing", response_model=RoleRoutingResponse)
def get_role_routing(
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RoleRoutingResponse:
    role_routing_store.bind_settings_repository(settings_repository)
    return RoleRoutingResponse(routing=role_routing_store.get())


@router.put("/config/role-routing", response_model=RoleRoutingResponse)
def update_role_routing(
    request: RoleRoutingRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> RoleRoutingResponse:
    role_routing_store.bind_settings_repository(settings_repository)
    try:
        routing = role_routing_store.update(request.routing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RoleRoutingResponse(routing=routing)


@router.get("/config/claude-model", response_model=ClaudeModelResponse)
def get_claude_model(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ClaudeModelResponse:
    return ClaudeModelResponse(
        model=settings_repository.get_claude_model(settings.claude_model),
    )


@router.put("/config/claude-model", response_model=ClaudeModelResponse)
def update_claude_model(
    request: ClaudeModelRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ClaudeModelResponse:
    try:
        model = settings_repository.set_claude_model(request.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ClaudeModelResponse(model=model)


@router.get("/config/claude-model/available", response_model=ClaudeModelsAvailableResponse)
def list_available_cloud_models(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClaudeModelsAvailableResponse:
    return ClaudeModelsAvailableResponse(
        models=list_available_claude_models(settings.claude_api_key),
    )


class ChatReasonerConfigResponse(BaseModel):
    mode: Literal["cloud", "local"]


class ChatReasonerConfigRequest(BaseModel):
    mode: Literal["cloud", "local"]


@router.get("/config/chat-reasoner", response_model=ChatReasonerConfigResponse)
def get_chat_reasoner_config(
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ChatReasonerConfigResponse:
    mode = settings_repository.get_json(CHAT_REASONER_MODE_SETTINGS_KEY, "cloud")
    if mode not in ("cloud", "local"):
        mode = "cloud"
    return ChatReasonerConfigResponse(mode=mode)


@router.put("/config/chat-reasoner", response_model=ChatReasonerConfigResponse)
def update_chat_reasoner_config(
    request: ChatReasonerConfigRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> ChatReasonerConfigResponse:
    settings_repository.set_json(CHAT_REASONER_MODE_SETTINGS_KEY, request.mode)
    return ChatReasonerConfigResponse(mode=request.mode)


@router.post("/config/claude-model/test", response_model=ClaudeConnectionTestResponse)
def test_claude_connection(
    request: ClaudeConnectionTestRequest,
) -> ClaudeConnectionTestResponse:
    api_key = request.api_key.strip()
    model = request.model.strip()
    if not api_key:
        return ClaudeConnectionTestResponse(
            valid=False,
            message="Claude API key is required.",
        )

    models = list_available_claude_models(api_key)
    if not models:
        return ClaudeConnectionTestResponse(
            valid=False,
            message="Could not fetch Claude models with this API key.",
        )

    if model and model not in models:
        return ClaudeConnectionTestResponse(
            valid=False,
            message=f"Connected, but {model} was not returned by Claude models.",
            models=models,
        )

    return ClaudeConnectionTestResponse(
        valid=True,
        message="Claude API key and model are reachable.",
        models=models,
    )


@router.get("/config/cloud-reasoner", response_model=CloudReasonerResponse)
def get_cloud_reasoner_config(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudReasonerResponse:
    model_id = selected_cloud_reasoner_id(settings, settings_repository)
    provider, _ = split_provider(model_id)
    return CloudReasonerResponse(
        model_id=model_id,
        provider=provider,
        providers=provider_options(settings),
        registry=cloud_model_inventory(settings, settings_repository),
    )


@router.put("/config/cloud-reasoner", response_model=CloudReasonerResponse)
def update_cloud_reasoner_config(
    request: CloudReasonerRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudReasonerResponse:
    try:
        cleaned = validate_cloud_reasoner_id(request.model_id)
        settings_repository.set_cloud_reasoner(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider, _ = split_provider(cleaned)
    return CloudReasonerResponse(
        model_id=cleaned,
        provider=provider,
        providers=provider_options(settings),
        registry=cloud_model_inventory(settings, settings_repository),
    )


@router.get("/api/config/cloud-models", response_model=CloudModelsResponse)
@router.get("/config/cloud-models", response_model=CloudModelsResponse)
def get_cloud_models(
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudModelsResponse:
    return CloudModelsResponse(
        models=[
            CloudModelResponse(**model)
            for model in cloud_model_inventory(settings, settings_repository)
        ],
    )


@router.post("/api/config/cloud-models", response_model=CloudModelResponse)
@router.post("/config/cloud-models", response_model=CloudModelResponse)
def create_cloud_model(
    request: CloudModelCreateRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudModelResponse:
    try:
        if _cloud_model_request_id(request) == default_anthropic_model_id(settings, settings_repository):
            raise CloudModelRegistryError("The default Claude model is built in and cannot be overwritten")
        model = add_cloud_model(
            settings_repository,
            label=request.label,
            provider=request.provider,
            model_id=request.model_id,
            api_key=request.api_key,
        )
    except (CloudModelRegistryError, SecretEncryptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CloudModelResponse(**model.to_public_dict(), deletable=True)


@router.post("/api/config/cloud-models/test", response_model=CloudModelTestResponse)
@router.post("/config/cloud-models/test", response_model=CloudModelTestResponse)
def test_cloud_model_connection(
    request: CloudModelTestRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudModelTestResponse:
    provider = request.provider.strip().lower()
    model_id = request.model_id.strip()
    qualified_id = f"{provider}:{model_id}"
    api_key = (request.api_key or "").strip()
    if not api_key:
        try:
            api_key = api_key_for_model(qualified_id, provider, settings, settings_repository)
        except Exception as exc:
            return CloudModelTestResponse(ok=False, message=str(exc))
    if not api_key:
        return CloudModelTestResponse(ok=False, message="API key is not configured")
    try:
        client = make_cloud_reasoner(qualified_id, api_key=api_key, timeout_sec=15.0)
        try:
            client._ensure_ready()
            client._complete("Reply with OK.")
        finally:
            client.close()
    except Exception as exc:
        return CloudModelTestResponse(ok=False, message=str(exc))
    return CloudModelTestResponse(ok=True, message="Connection OK")


@router.post("/api/config/cloud-models/list-models", response_model=CloudModelListResponse)
@router.post("/config/cloud-models/list-models", response_model=CloudModelListResponse)
def list_provider_models(
    request: CloudModelListRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CloudModelListResponse:
    """List the models a provider exposes for a given key, so the UI can offer a
    pick-list instead of making the user type a model id."""
    provider = request.provider.strip().lower()
    api_key = (request.api_key or "").strip() or api_key_for_provider(provider, settings)
    if not api_key:
        return CloudModelListResponse(ok=False, message="API key is not configured")
    try:
        if provider in ANTHROPIC_ALIASES:
            models = list_available_claude_models(api_key)
        elif provider in OPENAI_COMPAT_BASE_URLS:
            client = LMStudioClient(OPENAI_COMPAT_BASE_URLS[provider], api_key=api_key, timeout_sec=15.0)
            try:
                models = client.list_models()
            finally:
                client.close()
        else:
            return CloudModelListResponse(
                ok=False,
                message=f"Unknown provider '{provider}'. Supported: anthropic, "
                + ", ".join(sorted(OPENAI_COMPAT_BASE_URLS)),
            )
    except Exception as exc:
        return CloudModelListResponse(ok=False, message=str(exc))
    return CloudModelListResponse(ok=True, models=sorted(models))


@router.delete("/api/config/cloud-models/{model_id:path}", response_model=CloudModelDeleteResponse)
@router.delete("/config/cloud-models/{model_id:path}", response_model=CloudModelDeleteResponse)
def remove_cloud_model(
    model_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudModelDeleteResponse:
    if model_id == default_anthropic_model_id(settings, settings_repository):
        raise HTTPException(status_code=400, detail="The default Claude model cannot be deleted")
    deleted = delete_cloud_model(settings_repository, model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cloud model not found")
    return CloudModelDeleteResponse(deleted=True, model_id=model_id)


@router.get("/api/config/integrations", response_model=IntegrationCredentialsResponse)
@router.get("/config/integrations", response_model=IntegrationCredentialsResponse)
def list_integrations(
    repository: Annotated[IntegrationRepository, Depends(get_integration_repository)],
    provider: Literal["github", "gitlab", "slack"] | None = None,
) -> IntegrationCredentialsResponse:
    return IntegrationCredentialsResponse(
        integrations=[
            IntegrationCredentialResponse(**credential.to_public_dict())
            for credential in repository.list(provider)
        ]
    )


@router.post("/api/config/integrations", response_model=IntegrationCredentialResponse)
@router.post("/config/integrations", response_model=IntegrationCredentialResponse)
def upsert_integration(
    request: IntegrationCredentialRequest,
    repository: Annotated[IntegrationRepository, Depends(get_integration_repository)],
) -> IntegrationCredentialResponse:
    try:
        credential = repository.upsert(
            provider=request.provider,
            credential_id=request.id,
            label=request.label,
            scopes=request.scopes,
            token=request.token,
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IntegrationCredentialResponse(**credential.to_public_dict())


@router.delete(
    "/api/config/integrations/{provider}/{credential_id}",
    response_model=IntegrationDeleteResponse,
)
@router.delete(
    "/config/integrations/{provider}/{credential_id}",
    response_model=IntegrationDeleteResponse,
)
def delete_integration(
    provider: Literal["github", "gitlab", "slack"],
    credential_id: str,
    repository: Annotated[IntegrationRepository, Depends(get_integration_repository)],
) -> IntegrationDeleteResponse:
    deleted = repository.delete(provider, credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Integration credential not found")
    return IntegrationDeleteResponse(deleted=True, provider=provider, id=credential_id)


@router.get("/config/notifications", response_model=NotificationSettingsResponse)
def get_notification_settings(
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        webhook_url=get_notification_webhook(settings_repository),
    )


@router.put("/config/notifications", response_model=NotificationSettingsResponse)
def update_notification_settings(
    request: NotificationSettingsRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        webhook_url=set_notification_webhook(settings_repository, request.webhook_url),
    )


@router.get("/config/cloud-execution", response_model=CloudExecutionSettingsResponse)
def get_cloud_execution_settings(
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudExecutionSettingsResponse:
    return CloudExecutionSettingsResponse(
        allow_cloud_execution_model=bool(
            settings_repository.get_json(ALLOW_CLOUD_EXECUTION_SETTINGS_KEY, False),
        ),
    )


@router.put("/config/cloud-execution", response_model=CloudExecutionSettingsResponse)
def update_cloud_execution_settings(
    request: CloudExecutionSettingsRequest,
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
) -> CloudExecutionSettingsResponse:
    settings_repository.set_json(
        ALLOW_CLOUD_EXECUTION_SETTINGS_KEY,
        request.allow_cloud_execution_model,
    )
    return CloudExecutionSettingsResponse(
        allow_cloud_execution_model=request.allow_cloud_execution_model,
    )


@router.websocket("/tickets/{ticket_id}/logs")
async def ticket_logs(
    websocket: WebSocket,
    ticket_id: str,
    project_id: str | None = None,
) -> None:
    settings = get_settings()
    token = settings.haao_api_token.strip()
    if token and not _websocket_token_valid(websocket, token):
        await websocket.close(code=1008)
        return
    connection = connect(_sqlite_path(settings.database_url))
    try:
        identity = IdentityRepository(connection)
        from orchestrator.authz import AuthorizationError, AuthenticationError, require_action, resolve_auth_context

        try:
            context = resolve_auth_context(
                identity,
                user_id=websocket.headers.get("x-haao-user-id"),
                workspace_id=websocket.headers.get("x-haao-workspace-id") or project_id,
            )
            require_action(context, "read")
        except (AuthenticationError, AuthorizationError):
            await websocket.close(code=1008)
            return
    except Exception:
        connection.close()
        raise
    await websocket.accept()
    repository = TicketRepository(connection, project_id=project_id)
    sent = 0
    try:
        while True:
            logs = repository.logs_for_ticket(ticket_id)
            for log in logs[sent:]:
                await websocket.send_json(log)
            sent = len(logs)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    finally:
        connection.close()


def _chat_message_response(
    message: ChatMessage,
    repository: ChatRepository | None = None,
) -> ChatMessageResponse:
    attachments: list[ChatAttachmentResponse] = []
    if repository is not None and message.attachment_ids:
        attachments = [
            _chat_attachment_response(attachment)
            for attachment in repository.attachments_by_ids(
                message.project_id,
                message.attachment_ids,
            )
        ]
    return ChatMessageResponse(
        id=message.id,
        project_id=message.project_id,
        role=message.role,
        text=message.text,
        segment_id=message.segment_id,
        created_at=message.created_at,
        requirement_id=message.requirement_id,
        ticket_id=message.ticket_id,
        report_kind=message.report_kind,
        attachment_ids=message.attachment_ids,
        attachments=attachments,
    )


def _chat_attachment_response(attachment: ChatAttachment) -> ChatAttachmentResponse:
    return ChatAttachmentResponse(**attachment.to_public_dict())


def _chat_reasoner_prompt(
    summary: str,
    recent: list[ChatMessage],
    user_text: str,
    open_tickets: list[dict] | None = None,
) -> str:
    recent_json = [
        {
            "role": message.role,
            "text": message.text,
            "requirement_id": message.requirement_id,
            "ticket_id": message.ticket_id,
            "report_kind": message.report_kind,
            "created_at": message.created_at,
        }
        for message in recent
    ]
    board_json = open_tickets or []
    return (
        "You are the HAAO conversational product agent for an indie developer or small "
        "team. You talk with the user about what they want built and turn committed work "
        "into backlog proposals. A separate Tech Lead later splits each proposal into "
        "atomic tickets, so you do NOT break work down yourself.\n\n"
        "Decide whether the latest user message contains committed implementation work.\n"
        "FILE work_items when the user's intent to build, change, or fix something is "
        "clear — including reasonable inference from the recent conversation.\n"
        "Do NOT file (use an empty work_items array) for: questions, status checks, "
        "brainstorming or weighing options, vague wishes with no decision yet, or anything "
        "already covered by an open ticket on the board below. When you are genuinely "
        "unsure, do not file — ask a short clarifying question in 'reply' instead.\n\n"
        "Granularity: each work_item is ONE requirement-level piece (a feature, a fix, a "
        "change) — never pre-split into tiny tasks. Emit multiple work_items only when the "
        "user clearly described separate, unrelated pieces of work.\n"
        "'title' is a short label. 'prompt' is a clear, self-contained description of the "
        "work for the Tech Lead (what to build and what 'done' means), written in the "
        "user's language.\n\n"
        "'reply' is a short, conversational message. When you file work_items the system "
        "automatically tells the user what is being filed, so 'reply' must NOT restate the "
        "items — keep it a brief follow-up (e.g. a clarifying question) or empty.\n"
        "'updated_summary' is a rolling summary of the project conversation (decisions, "
        "what has been filed, open threads). Return the new summary, or null if unchanged.\n\n"
        "Return ONLY valid JSON (no markdown, no code fences, no text outside the object) "
        "with this exact shape:\n"
        "{"
        "\"reply\":\"short user-facing reply\","
        "\"work_items\":[{\"title\":\"short title\",\"prompt\":\"requirement description\"}],"
        "\"updated_summary\":\"running summary or null\""
        "}\n\n"
        f"Running summary:\n{summary.strip() or '(empty)'}\n\n"
        f"Open tickets on the board (avoid duplicating these):\n"
        f"{json.dumps(board_json, ensure_ascii=False)}\n\n"
        f"Recent messages JSON:\n{json.dumps(recent_json, ensure_ascii=False)}\n\n"
        f"Latest user message:\n{user_text.strip()}"
    )


def _cloud_model_request_id(request: CloudModelCreateRequest) -> str:
    provider = request.provider.strip().lower()
    if provider in ANTHROPIC_ALIASES or provider == "claude":
        provider = "anthropic"
    return f"{provider}:{request.model_id.strip()}"


def _websocket_token_valid(websocket: WebSocket, token: str) -> bool:
    authorization = websocket.headers.get("authorization")
    query_token = websocket.query_params.get("token")
    return authorization == f"Bearer {token}" or query_token == token


def _sqlite_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported")
    path = database_url[len(prefix) :]
    if path.startswith("./"):
        return str(PROJECT_ROOT / path[2:])
    return path


def _resolve_project(
    repository: ProjectRepository,
    project_id: str | None,
) -> Project:
    if not project_id:
        default_project = repository.get("default")
        if default_project is not None:
            return default_project
        return Project(
            id="default",
            name="HAAO",
            path=str(PROJECT_ROOT),
            default_branch="main",
        )
    project = repository.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project


def _ticket_project_id(ticket: Ticket) -> str:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        project_id = metadata.get("project_id")
        if isinstance(project_id, str) and project_id:
            return project_id
    return "default"


def _ticket_requirement_id(ticket: Ticket) -> str | None:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        requirement_id = metadata.get("requirement_id")
        if isinstance(requirement_id, str) and requirement_id:
            return requirement_id
    return None


def _ticket_run_id(ticket: Ticket) -> str | None:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        run_id = metadata.get("last_run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None


def _runner_token_from_request(request: Request) -> str:
    header = request.headers.get("x-haao-runner-token", "")
    if header:
        return header
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return ""


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _require_runner(request: Request, repository: RunnerRepository) -> RunnerTokenRecord:
    token = _runner_token_from_request(request)
    runner = repository.authenticate(token) if token else None
    if runner is None:
        raise HTTPException(status_code=401, detail="Invalid or missing runner token")
    return runner


def _ticket_depends_on(ticket: Ticket) -> list[str]:
    dependencies: list[str] = []
    for dependency in [*ticket.dependencies, *ticket.depends_on]:
        if dependency not in dependencies:
            dependencies.append(dependency)
    return dependencies


def _ticket_ready_state(
    ticket: Ticket,
    tickets_by_id: dict[str, Ticket],
    active_leases: dict[str, Ticket],
) -> str:
    if ticket.status in {"done", "abandoned", "split"}:
        return "terminal"
    if ticket.status != "ready":
        return "not_ready"
    for dependency_id in _ticket_depends_on(ticket):
        dependency = tickets_by_id.get(dependency_id)
        if dependency is None or dependency.status != "done":
            return "waiting_dependencies"
        metadata = dependency.metadata.model_dump(mode="json") if dependency.metadata else {}
        if metadata.get("git_branch") and not metadata.get("git_merge_commit"):
            return "waiting_dependencies"
    target_files = set(ticket.task.target_files)
    for leased in active_leases.values():
        if leased.id != ticket.id and target_files.intersection(leased.task.target_files):
            return "conflict"
    return "ready"


def _ticket_lease_payload(ticket: Ticket) -> dict | None:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    worker_id = metadata.get("lease_worker_id")
    if not isinstance(worker_id, str) or not worker_id:
        return None
    return {
        "worker_id": worker_id,
        "expires_at": metadata.get("lease_expires_at"),
        "heartbeat_at": metadata.get("lease_heartbeat_at"),
        "ttl_sec": metadata.get("lease_ttl_sec"),
    }


def _effective_sandbox_mode(project_mode: str, settings: Settings) -> str:
    override = (getattr(settings, "haao_sandbox_mode", "") or "").strip().lower()
    if override in {"auto", "docker", "unshare", "none"}:
        return override
    return project_mode if project_mode in {"auto", "docker", "unshare", "none"} else "auto"


def _split_child_ticket_payloads(
    ticket: Ticket,
    feedback: str,
    repository: TicketRepository,
) -> list[dict]:
    parent = ticket.to_dict()
    project_id = _ticket_project_id(ticket)
    next_number = repository.next_ticket_number(project_id)
    target_files = list(parent["task"]["target_files"])
    context_files = parent.get("context", {}).get("files", [])
    children: list[dict] = []
    for index, target_file in enumerate(target_files or parent["task"]["target_files"], start=1):
        payload = copy.deepcopy(parent)
        payload["id"] = f"T-{next_number:03d}"
        next_number += 1
        payload["title"] = _child_split_title(ticket.title, index, len(target_files), target_file)
        payload["status"] = TicketStatus.BACKLOG.value
        payload["dependencies"] = []
        payload["task"]["target_files"] = [target_file]
        payload["task"]["description"] = (
            f"{ticket.task.description}\n\nSplit from {ticket.id}. "
            f"Product Owner split guidance: {feedback}"
        ).strip()
        payload["context"]["files"] = [
            file
            for file in context_files
            if isinstance(file, dict) and file.get("path") == target_file
        ] or copy.deepcopy(context_files)
        payload["result"] = {"outcome": "pending"}
        payload["audit"] = {"verdict": "pending", "feedback": "", "reviewed_by": ""}
        metadata = payload.setdefault("metadata", {})
        metadata["project_id"] = project_id
        metadata["parent_ticket_id"] = ticket.id
        metadata["split_from"] = ticket.id
        metadata["split_feedback"] = feedback
        metadata["split_child_index"] = index
        metadata["needs_approval"] = True
        metadata.pop("child_ticket_ids", None)
        metadata.pop("abandoned", None)
        metadata.pop("abandoned_at", None)
        metadata.pop("abandon_reason", None)
        children.append(payload)
    return children


def _child_split_title(title: str, index: int, total: int, target_file: str) -> str:
    suffix = f" ({index}/{total}: {target_file})" if total > 1 else f" ({target_file})"
    max_prefix = max(1, 120 - len(suffix))
    return (title[:max_prefix].rstrip() + suffix)[:120]


def _body_project_manual_ticket_service(
    service: ManualTicketService,
    project_id: str | None,
    repository: TicketRepository,
    project_repository: ProjectRepository,
    settings_repository: SettingsRepository,
) -> ManualTicketService:
    if not project_id:
        return service
    project = _resolve_project(project_repository, project_id)
    repo_root = Path(project.path)
    role_routing_store.bind_settings_repository(settings_repository)
    return ManualTicketService(
        repository.scoped(project.id),
        ContextInjector(repo_root),
        project_id=project.id,
        settings_repository=settings_repository,
    )


def _body_project_requirement_service(
    service: RequirementService,
    project_id: str | None,
    repository: TicketRepository,
    requirement_repository: RequirementRepository,
    tech_lead: ClaudeTechLeadClient,
    project_repository: ProjectRepository,
    settings_repository: SettingsRepository,
) -> RequirementService:
    if not project_id:
        return service
    if not isinstance(service, RequirementService):
        return service
    project = _resolve_project(project_repository, project_id)
    repo_root = Path(project.path)
    return RequirementService(
        repository.scoped(project.id),
        requirement_repository.scoped(project.id),
        tech_lead,
        repo_root=repo_root,
        project_id=project.id,
        context_injector=ContextInjector(repo_root),
        settings_repository=settings_repository,
    )


def _run_eval_background(database_url: str, eval_id: str, settings: Settings) -> None:
    connection = connect(_sqlite_path(database_url))
    try:
        EvalService(EvalRunRepository(connection)).run_to_completion(eval_id, settings=settings)
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()
