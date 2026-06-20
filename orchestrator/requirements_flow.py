from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.context.conventions import detect_conventions, detect_test_command
from orchestrator.context.injector import ContextInjector
from orchestrator.cloud_usage import CloudUsage, apply_usage_to_requirement
from orchestrator.context.repo_context import build_enriched_repo_context
from orchestrator.context.scope import validate_scope_paths
from orchestrator.db.sqlite import RequirementRepository, SettingsRepository, TicketRepository
from orchestrator.model_instructions import tech_lead_additional_instructions
from orchestrator.model_policy import enforce_local_execution_model
from orchestrator.models.requirement import Requirement, RequirementStatus
from orchestrator.models.ticket import Ticket


class Decomposer(Protocol):
    def decompose(
        self,
        requirement: str,
        repo_context: str,
        *,
        scope_paths: list[str] | None = None,
        constraints: list[str] | None = None,
        acceptance_notes: str = "",
        attachments: list[dict] | None = None,
        intent: str = "feature",
        scale: str | None = None,
        granularity: str = "balanced",
        allow_new_files: bool = False,
        test_command: str = "",
        repo_conventions: str = "",
        priority: str = "medium",
    ) -> list[dict]:
        ...


@dataclass(frozen=True)
class RequirementPreviewResult:
    requirement: Requirement
    proposed_tickets: list[Ticket]


@dataclass(frozen=True)
class RequirementConfirmResult:
    requirement: Requirement
    tickets: list[Ticket]


@dataclass(frozen=True)
class RequirementResult:
    tickets: list[Ticket]


class RequirementService:
    def __init__(
        self,
        ticket_repository: TicketRepository,
        requirement_repository: RequirementRepository,
        decomposer: Decomposer,
        *,
        repo_root: str | Path,
        project_id: str | None = None,
        context_injector: ContextInjector | None = None,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.ticket_repository = ticket_repository
        self.requirement_repository = requirement_repository
        self.decomposer = decomposer
        self.repo_root = Path(repo_root).resolve()
        self.project_id = project_id or getattr(ticket_repository, "project_id", None) or "default"
        self.context_injector = context_injector or ContextInjector(self.repo_root)
        self.settings_repository = settings_repository

    def decompose_preview(self, requirement: Requirement) -> RequirementPreviewResult:
        if not requirement.prompt.strip():
            raise ValueError("Requirement prompt cannot be empty")

        validate_scope_paths(requirement.scope_paths)
        requirement = requirement.model_copy(
            update={
                "id": requirement.id or self.requirement_repository.next_id(),
                "project_id": requirement.project_id or self.project_id,
                "status": RequirementStatus.DECOMPOSING,
                "proposed_tickets": [],
                "generated_ticket_ids": [],
            }
        )
        stored = self.requirement_repository.create(requirement)

        repo_context = build_enriched_repo_context(self.repo_root, stored.scope_paths)
        detected_test_command = detect_test_command(self.repo_root)
        test_command = stored.test_command or detected_test_command
        repo_conventions = detect_conventions(self.repo_root)
        if test_command and f"Preferred test command override: {test_command}" not in repo_conventions:
            repo_conventions = f"{repo_conventions}\n- Preferred test command override: {test_command}"
        ticket_dicts = _call_decomposer(
            self.decomposer,
            stored.prompt,
            repo_context,
            scope_paths=stored.scope_paths,
            constraints=stored.constraints,
            acceptance_notes=stored.acceptance_notes,
            attachments=[attachment.model_dump(mode="json") for attachment in stored.attachments],
            intent=stored.intent,
            scale=stored.scale,
            granularity=stored.granularity,
            allow_new_files=stored.allow_new_files,
            test_command=test_command,
            repo_conventions=repo_conventions,
            priority=stored.priority,
            additional_instructions=tech_lead_additional_instructions(self.settings_repository),
        )

        proposed_tickets: list[Ticket] = []
        for ticket_dict in ticket_dicts:
            ticket_dict["status"] = "backlog"
            enforce_local_execution_model(ticket_dict, repository=self.settings_repository)
            _merge_requirement_guidance(ticket_dict, stored, persisted=False)
            ticket = self.context_injector.inject(Ticket.from_dict(ticket_dict))
            proposed_tickets.append(ticket)

        usage = getattr(self.decomposer, "last_usage", CloudUsage())
        if not isinstance(usage, CloudUsage):
            usage = CloudUsage()
        if usage.input_tokens or usage.output_tokens:
            stored = apply_usage_to_requirement(stored, usage)

        stored = stored.model_copy(
            update={
                "status": RequirementStatus.PREVIEW_READY,
                "proposed_tickets": [ticket.to_dict() for ticket in proposed_tickets],
            }
        )
        stored = self.requirement_repository.save(stored)
        return RequirementPreviewResult(
            requirement=stored,
            proposed_tickets=proposed_tickets,
        )

    def next_requirement_id(self) -> str:
        return self.requirement_repository.next_id()

    def confirm(
        self,
        requirement_id: str,
        tickets: list[dict] | None = None,
    ) -> RequirementConfirmResult:
        requirement = self._require_requirement(requirement_id)
        if RequirementStatus(requirement.status) != RequirementStatus.PREVIEW_READY:
            raise ValueError("Only preview_ready requirements can be confirmed")

        ticket_payloads = tickets if tickets is not None else requirement.proposed_tickets
        if not ticket_payloads:
            raise ValueError("At least one ticket is required to confirm a requirement")

        ticket_payloads = self._renumber_ticket_payloads(ticket_payloads)

        created_tickets: list[Ticket] = []
        for ticket_dict in ticket_payloads:
            ticket_dict["status"] = "ready"
            enforce_local_execution_model(ticket_dict, repository=self.settings_repository)
            _merge_requirement_guidance(ticket_dict, requirement, persisted=True)
            ticket = self.context_injector.inject(Ticket.from_dict(ticket_dict))
            created_tickets.append(self.ticket_repository.create(ticket))

        requirement = requirement.model_copy(
            update={
                "status": RequirementStatus.CONFIRMED,
                "generated_ticket_ids": [ticket.id for ticket in created_tickets],
            }
        )
        requirement = self.requirement_repository.save(requirement)
        return RequirementConfirmResult(
            requirement=requirement,
            tickets=created_tickets,
        )

    def discard(self, requirement_id: str) -> Requirement:
        requirement = self._require_requirement(requirement_id)
        if RequirementStatus(requirement.status) == RequirementStatus.CONFIRMED:
            raise ValueError("Confirmed requirements cannot be discarded")
        requirement = requirement.model_copy(update={"status": RequirementStatus.DISCARDED})
        return self.requirement_repository.save(requirement)

    def submit(self, requirement: str, repo_context: str = "") -> RequirementResult:
        """Compatibility wrapper for the old one-shot endpoint.

        New callers should use decompose_preview() and confirm() so the Product Owner
        can inspect proposed tickets before they reach the board.
        """
        draft = Requirement(
            id=self.requirement_repository.next_id(),
            project_id=self.project_id,
            prompt=requirement,
            acceptance_notes=repo_context,
        )
        preview = self.decompose_preview(draft)
        confirmed = self.confirm(
            preview.requirement.id,
            [ticket.to_dict() for ticket in preview.proposed_tickets],
        )
        return RequirementResult(tickets=confirmed.tickets)

    def list_requirements(self) -> list[Requirement]:
        return self.requirement_repository.list()

    def get_requirement(self, requirement_id: str) -> Requirement | None:
        return self.requirement_repository.get(requirement_id)

    def _require_requirement(self, requirement_id: str) -> Requirement:
        requirement = self.requirement_repository.get(requirement_id)
        if requirement is None:
            raise KeyError(f"Requirement not found: {requirement_id}")
        return requirement

    def _renumber_ticket_payloads(self, ticket_payloads: list[dict]) -> list[dict]:
        next_number = self.ticket_repository.next_ticket_number()
        id_map: dict[str, str] = {}
        renumbered: list[dict] = []

        for ticket_dict in ticket_payloads:
            payload = copy.deepcopy(ticket_dict)
            original_id = payload.get("id")
            new_id = f"T-{next_number:03d}"
            next_number += 1

            if isinstance(original_id, str):
                id_map[original_id] = new_id
                metadata = payload.setdefault("metadata", {})
                metadata.setdefault("source_ticket_id", original_id)
            payload["id"] = new_id
            renumbered.append(payload)

        for payload in renumbered:
            dependencies = payload.get("dependencies")
            if isinstance(dependencies, list):
                payload["dependencies"] = [
                    id_map.get(dependency, dependency)
                    for dependency in dependencies
                ]

        return renumbered


def build_repo_context(repo_root: Path, scope_paths: list[str]) -> str:
    return build_enriched_repo_context(repo_root, scope_paths)


def _call_decomposer(
    decomposer: Decomposer,
    requirement: str,
    repo_context: str,
    **kwargs: object,
) -> list[dict]:
    signature = inspect.signature(decomposer.decompose)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        accepted_kwargs = kwargs
    else:
        accepted_kwargs = {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
    return decomposer.decompose(requirement, repo_context, **accepted_kwargs)


def _merge_requirement_guidance(
    ticket_dict: dict,
    requirement: Requirement,
    *,
    persisted: bool,
) -> None:
    metadata = ticket_dict.setdefault("metadata", {})
    metadata["requirement_id"] = requirement.id
    if requirement.project_id:
        metadata["project_id"] = requirement.project_id
    metadata["needs_approval"] = not persisted

    task = ticket_dict.setdefault("task", {})
    constraints = list(task.get("constraints", []))
    for constraint in requirement.constraints:
        if constraint not in constraints:
            constraints.append(constraint)
    task["constraints"] = constraints

    if requirement.acceptance_notes:
        definition = ticket_dict.setdefault("definition_of_done", {})
        criteria = list(definition.get("acceptance_criteria", []))
        if requirement.acceptance_notes not in criteria:
            criteria.append(requirement.acceptance_notes)
        definition["acceptance_criteria"] = criteria

    if not requirement.allow_new_files:
        constraints = list(task.get("constraints", []))
        constraint = "Do not create new files; only modify existing target files."
        if constraint not in constraints:
            constraints.append(constraint)
        task["constraints"] = constraints
