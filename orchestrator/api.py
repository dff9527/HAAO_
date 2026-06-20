from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from clients.lmstudio import LMStudioClient
from clients.claude_po import list_available_claude_models
from clients.cloud_reasoner import BaseCloudReasoner
from clients.factory import split_provider
from clients.tech_lead import ClaudeTechLeadClient
from orchestrator.auto_orchestrator import AutoOrchestrator
from orchestrator.auto_worker import auto_worker
from orchestrator.cloud_reasoner_config import (
    build_cloud_reasoner,
    provider_options,
    selected_cloud_reasoner_id,
    validate_cloud_reasoner_id,
)
from orchestrator.config import Settings, get_settings
from orchestrator.context.conventions import detect_conventions, detect_test_command
from orchestrator.context.injector import ContextInjector
from orchestrator.db.sqlite import (
    AmbiguousTicketError,
    DuplicateTicketError,
    ProjectRepository,
    RequirementRepository,
    SettingsRepository,
    TicketDeletionError,
    TicketRepository,
    connect,
)
from orchestrator.escalation import EscalationService
from orchestrator.diff_review import DiffReviewService
from orchestrator.execution_loop import ExecutionLoop
from orchestrator.execution_registry import execution_key, execution_registry
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.git_flow import GitTicketFlow, now_iso
from orchestrator.manual_ticket_flow import (
    ManualTicketCreatePayload,
    ManualTicketError,
    ManualTicketService,
)
from orchestrator.model_policy import local_execution_model
from orchestrator.local_models import (
    LocalModelEndpoint,
    cache_local_models,
    discover_local_models,
    get_local_model_endpoints,
    set_local_model_endpoints,
)
from orchestrator.models.project import Project
from orchestrator.models.requirement import Requirement, RequirementAttachment, RequirementStatus
from orchestrator.models.ticket import TicketStatus
from orchestrator.model_instructions import normalize_model_settings_id
from orchestrator.notifications import get_notification_webhook, set_notification_webhook
from orchestrator.requirements_flow import RequirementService
from orchestrator.review_flow import ReviewService
from orchestrator.role_routing import role_routing_store
from orchestrator.runner.dod_runner import TestRunner
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


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    default_branch: str = "main"
    id: str | None = None


class ProjectSettingsRequest(BaseModel):
    env: dict[str, str] | None = None
    setup_cmd: str | None = None
    cleanup_cmd: str | None = None
    default_branch: str | None = None


class ProjectResponse(BaseModel):
    project: dict


class ProjectConventionsResponse(BaseModel):
    project_id: str
    test_command: str
    conventions: str


class ProjectsResponse(BaseModel):
    projects: list[dict]


class DeleteProjectResponse(BaseModel):
    deleted: bool
    project_id: str


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


class NotificationSettingsRequest(BaseModel):
    webhook_url: str = ""


class NotificationSettingsResponse(BaseModel):
    webhook_url: str


class TicketResponse(BaseModel):
    ticket: dict


class TicketsResponse(BaseModel):
    tickets: list[dict]


class OperationResponse(BaseModel):
    ticket: dict


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
    allow_dirty_workspace: bool = False


class AutoWorkerResponse(BaseModel):
    running: bool
    interval_sec: float
    max_cycles_per_tick: int
    allow_dirty_workspace: bool
    last_started_at: str | None
    last_run_at: str | None
    last_error: str
    project_id: str | None = None


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
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    settings_repository: Annotated[SettingsRepository, Depends(get_settings_repository)],
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
            setup_cmd=project.setup_cmd,
            cleanup_cmd=project.cleanup_cmd,
        ),
        settings_repository=settings_repository,
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
) -> OperationResponse:
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
    return OperationResponse(ticket=updated.to_dict())


@router.post("/tickets/{ticket_id}/escalate", response_model=OperationResponse)
def escalate_ticket(
    ticket_id: str,
    request: EscalateRequest,
    repository: Annotated[TicketRepository, Depends(get_repository)],
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
        setup_cmd=project.setup_cmd,
        cleanup_cmd=project.cleanup_cmd,
        interval_sec=request.interval_sec,
        max_cycles_per_tick=request.max_cycles_per_tick,
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
    )


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


@router.websocket("/tickets/{ticket_id}/logs")
async def ticket_logs(
    websocket: WebSocket,
    ticket_id: str,
    project_id: str | None = None,
) -> None:
    await websocket.accept()
    settings = get_settings()
    connection = connect(_sqlite_path(settings.database_url))
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


def _now() -> str:
    return datetime.now(UTC).isoformat()
