from __future__ import annotations

import inspect
import re
import subprocess
import uuid
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Protocol

from clients.lmstudio import ChatMessage
from orchestrator.cloud_usage import CloudUsage, apply_usage_to_requirement
from orchestrator.config import get_settings
from orchestrator.db.sqlite import (
    PromptVersionRepository,
    RequirementRepository,
    RunEventRepository,
    SettingsRepository,
    TicketRepository,
)
from orchestrator.execution_resolver import resolve_execution_client
from orchestrator.execution_safety import GitWorkspaceGuard, derive_diff_stats, normalize_repo_path
from orchestrator.model_policy import next_local_fallback_model
from orchestrator.models.ticket import Result, Ticket, TicketStatus
from orchestrator.runner.dod_runner import TestRunResult, TestRunner
from orchestrator.runner.sandbox import SandboxAudit
from orchestrator.execution_registry import ExecutionCancelledError, execution_key, execution_registry
from orchestrator.git_flow import now_iso
from orchestrator.local_inference_probe import (
    LocalInferenceContextProbeStop,
    log_local_inference_context,
    should_stop_after_context_probe,
)
from orchestrator.manual_ticket_flow import is_unverified_ticket
from orchestrator.state_machine import InvalidTransitionError, TicketStateService
from orchestrator.notifications import NotificationService
from orchestrator.context.injector import estimate_tokens
from orchestrator.context.untrusted import UNTRUSTED_CONTEXT_INSTRUCTION
from orchestrator.prompt_registry import record_prompt_version
from orchestrator.redaction import current_known_secrets, redact_text


DEFAULT_LOCAL_MAX_OUTPUT_TOKENS = 4096
DEFAULT_PATCH_MODE_THRESHOLD_TOKENS = 2048
OUTPUT_TOKEN_MULTIPLIER = 1.5
OUTPUT_TOKEN_OVERHEAD = 512


class LocalModel(Protocol):
    def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage | dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        ...


class WholeFileWriteError(RuntimeError):
    """Raised when model output cannot be safely written to the workspace."""


class SearchReplaceParseError(WholeFileWriteError):
    """Raised when model output is not a valid SEARCH/REPLACE response."""


class SearchReplaceBlockError(WholeFileWriteError):
    """Raised when a SEARCH block cannot be applied exactly once."""

    def __init__(self, block_index: int, reason: str, match_count: int) -> None:
        self.block_index = block_index
        self.reason = reason
        self.match_count = match_count
        if reason == "not_found":
            message = f"SEARCH block {block_index} was not found in the current file"
        else:
            message = (
                f"SEARCH block {block_index} is ambiguous: "
                f"found {match_count} exact matches in the current file"
            )
        super().__init__(message)


# Guards against patch edits that delete most of a file (R-112.1).
DESTRUCTIVE_MAX_SHRINK_RATIO = 0.4   # reject if the file shrinks by more than this
DESTRUCTIVE_MIN_CHARS = 400          # only guard files large enough for shrink to matter
DESTRUCTIVE_BLOCK_COVERAGE = 0.7     # a single SEARCH may not span more of the file than this
DESTRUCTIVE_MIN_LINES = 30           # only apply the coverage guard to non-trivial files


class DestructiveEditError(WholeFileWriteError):
    """Raised when a SEARCH/REPLACE edit would delete a large fraction of the file."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            "Edit rejected as destructive: " + detail + ". Emit a minimal SEARCH/REPLACE "
            "block that changes only the necessary lines — do not replace a whole function "
            "or file with a stub."
        )


@dataclass(frozen=True)
class ExecutionResult:
    ticket: Ticket
    passed: bool
    escalated: bool = False


@dataclass(frozen=True)
class ActiveRunContext:
    project_id: str
    requirement_id: str | None
    ticket_id: str
    run_id: str


class WholeFileWriter:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def write(self, target_file: str, content: str, allowed_target_files: list[str]) -> None:
        content = strip_code_fence(content)
        if not content:
            raise WholeFileWriteError("Local model returned empty file content")

        normalized_target = normalize_repo_path(target_file)
        allowed = {normalize_repo_path(path) for path in allowed_target_files}
        if normalized_target not in allowed:
            raise WholeFileWriteError(
                f"Target file {normalized_target} is outside ticket.task.target_files"
            )

        destination = (self.repo_root / normalized_target).resolve()
        if not destination.is_relative_to(self.repo_root):
            raise WholeFileWriteError(
                f"Target file {normalized_target} resolves outside repo_root"
            )
        if not destination.parent.exists():
            raise WholeFileWriteError(
                f"Parent directory does not exist for target file {normalized_target}"
            )

        destination.write_text(content, encoding="utf-8")


class ExecutionLoop:
    def __init__(
        self,
        repository: TicketRepository,
        state_service: TicketStateService,
        local_model: LocalModel,
        *,
        repo_root: str | Path,
        test_runner: TestRunner | None = None,
        file_writer: WholeFileWriter | None = None,
        workspace_guard: GitWorkspaceGuard | None = None,
        settings_repository: SettingsRepository | None = None,
        requirement_repository: RequirementRepository | None = None,
        max_output_tokens: int = DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
        patch_mode_threshold_tokens: int = DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
    ) -> None:
        self.repository = repository
        self.state_service = state_service
        self.local_model = local_model
        self.repo_root = Path(repo_root).resolve()
        self.test_runner = test_runner
        self.file_writer = file_writer
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)
        self.settings_repository = settings_repository
        self.requirement_repository = requirement_repository
        self.run_events = RunEventRepository(repository.connection)
        self.prompt_versions = PromptVersionRepository(repository.connection)
        self._active_run_context: ActiveRunContext | None = None
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if patch_mode_threshold_tokens < 1:
            raise ValueError("patch_mode_threshold_tokens must be positive")
        self.max_output_tokens = max_output_tokens
        self.patch_mode_threshold_tokens = patch_mode_threshold_tokens

    def run_ticket(self, ticket_id: str) -> ExecutionResult:
        registry_key = execution_key(self.repository.project_id, ticket_id)
        cancel_event = execution_registry.register(registry_key)
        run_id = f"RUN-{uuid.uuid4().hex}"
        try:
            ticket = self._prepare_ticket(ticket_id)
            project_id = _ticket_project_id(ticket)
            requirement_id = _ticket_requirement_id(ticket)
            run_context = ActiveRunContext(
                project_id=project_id,
                requirement_id=requirement_id,
                ticket_id=ticket.id,
                run_id=run_id,
            )
            self._active_run_context = run_context
            reasoner_prompt_version = self._record_execution_prompt_version()
            ticket = self._mark_run_context(
                ticket,
                run_context,
                reasoner_prompt_version=reasoner_prompt_version,
            )
            self.run_events.append_run_event(
                project_id=project_id,
                requirement_id=requirement_id,
                ticket_id=ticket.id,
                run_id=run_id,
                event_type="run_started",
                model_id=ticket.execution.assigned_model,
                payload={
                    "status": ticket.status,
                    "model_id": ticket.execution.assigned_model,
                    "reasoner_prompt_version": reasoner_prompt_version,
                },
            )
            with self.workspace_guard.worktree_for_ticket(ticket.id) as worktree_root:
                execution_registry.set_worktree(registry_key, worktree_root)
                result = self._run_ticket_in_worktree(ticket, worktree_root, cancel_event, registry_key)
                self.run_events.append_run_event(
                    project_id=project_id,
                    requirement_id=requirement_id,
                    ticket_id=ticket.id,
                    run_id=run_id,
                    event_type="run_finished",
                    model_id=result.ticket.execution.assigned_model,
                    payload={"passed": result.passed, "escalated": result.escalated},
                )
                return result
        except ExecutionCancelledError:
            ticket = self._require_ticket(ticket_id)
            ticket_json = ticket.to_dict()
            ticket_json["status"] = TicketStatus.READY.value
            ticket_json["execution"]["attempts"] = 0
            ticket_json["result"] = Result(outcome="pending").model_dump(mode="json", exclude_none=True)
            saved = self.repository.save(Ticket.from_dict(ticket_json))
            self.repository.append_log(ticket_id, "Execution cancelled by user", level="warn")
            self.run_events.append_run_event(
                project_id=_ticket_project_id(saved),
                requirement_id=_ticket_requirement_id(saved),
                ticket_id=saved.id,
                run_id=run_id,
                event_type="run_finished",
                model_id=saved.execution.assigned_model,
                payload={"passed": False, "cancelled": True},
            )
            return ExecutionResult(ticket=saved, passed=False)
        except Exception as exc:
            context = self._active_run_context
            self.run_events.append_run_event(
                project_id=context.project_id if context else self.repository.project_id or "default",
                requirement_id=context.requirement_id if context else None,
                ticket_id=context.ticket_id if context else ticket_id,
                run_id=run_id,
                event_type="error",
                payload={"error": str(exc)},
            )
            raise
        finally:
            self._active_run_context = None
            execution_registry.unregister(registry_key)

    def _check_cancelled(self, registry_key: str) -> None:
        if execution_registry.is_cancelled(registry_key):
            raise ExecutionCancelledError(f"Execution cancelled for {registry_key}")

    def _run_ticket_in_worktree(
        self,
        ticket: Ticket,
        worktree_root: Path,
        cancel_event,
        registry_key: str,
    ) -> ExecutionResult:
        file_writer = self.file_writer or WholeFileWriter(worktree_root)
        test_runner = self._test_runner_for(worktree_root)
        while True:
            self._check_cancelled(registry_key)
            if ticket.execution.attempts > 0:
                self._reset_worktree_with_event(worktree_root, ticket, reason="retry")
                self.repository.append_log(ticket.id, "Reset ticket worktree before retry")

            # The persisted ticket contains the dispatch-time snapshot. Refresh
            # target files from the actual worktree at the beginning of every
            # attempt so retries start from the reset baseline, not from a prior
            # attempt's generated content.
            ticket = _refresh_target_context(ticket, worktree_root)

            write_error: WholeFileWriteError | None = None
            try:
                for target_file in ticket.task.target_files:
                    self._check_cancelled(registry_key)
                    file_content = _target_file_content(ticket, target_file)
                    required_tokens = required_rewrite_output_tokens(file_content)
                    effective_patch_threshold = min(
                        self.patch_mode_threshold_tokens,
                        self.max_output_tokens,
                    )
                    patch_mode = required_tokens > effective_patch_threshold
                    output_tokens = self.max_output_tokens if patch_mode else required_tokens
                    edit_mode = "SEARCH/REPLACE patch" if patch_mode else "whole-file rewrite"
                    self.repository.append_log(
                        ticket.id,
                        f"Dispatching {target_file} to local model for {edit_mode} "
                        f"(max_tokens={output_tokens})",
                    )
                    prompt = (
                        build_file_patch_prompt(ticket, target_file)
                        if patch_mode
                        else build_file_rewrite_prompt(ticket, target_file)
                    )
                    log_local_inference_context(
                        "before",
                        ticket=ticket,
                        target_file=target_file,
                        prompt=prompt,
                    )
                    if should_stop_after_context_probe():
                        raise LocalInferenceContextProbeStop(
                            f"Context probe captured for {ticket.id}:{target_file}"
                        )
                    completion_kwargs = {
                        "model": ticket.execution.assigned_model,
                        "messages": [
                            ChatMessage(
                                role="user",
                                content=prompt,
                            )
                        ],
                        "temperature": 0.2,
                    }
                    execution_client = resolve_execution_client(
                        ticket.execution.assigned_model,
                        local_client=self.local_model,
                        settings_repository=self.settings_repository,
                    )
                    if _supports_max_tokens(execution_client):
                        completion_kwargs["max_tokens"] = output_tokens
                    try:
                        model_output = execution_client.chat_completion(
                            **completion_kwargs,
                        )
                        usage = _usage_from_client(execution_client)
                        ticket = self._record_cloud_execution_usage(ticket, execution_client)
                        self._record_model_call(
                            ticket,
                            prompt=prompt,
                            response=model_output,
                            target_file=target_file,
                            edit_mode=edit_mode,
                            max_tokens=output_tokens,
                            usage=usage,
                        )
                    finally:
                        close = getattr(execution_client, "close", None)
                        if callable(close):
                            close()
                    log_local_inference_context(
                        "after",
                        ticket=ticket,
                        target_file=target_file,
                        prompt=prompt,
                        response=model_output,
                    )
                    if patch_mode:
                        normalized_target = normalize_repo_path(target_file)
                        destination = (worktree_root / normalized_target).resolve()
                        if not destination.is_relative_to(worktree_root.resolve()):
                            raise WholeFileWriteError(
                                f"Target file {normalized_target} resolves outside repo_root"
                            )
                        apply_search_replace_blocks(destination, model_output)
                    else:
                        file_writer.write(target_file, model_output, ticket.task.target_files)
                    # Target files are generated sequentially. Feed the file we
                    # just wrote into the in-memory context so the next rewrite
                    # (commonly the tests) sees the current implementation.
                    ticket = _refresh_target_context(
                        ticket,
                        worktree_root,
                        target_files=[target_file],
                    )
            except WholeFileWriteError as exc:
                write_error = exc

            ticket = self.state_service.move(ticket.id, TicketStatus.TESTING).ticket
            self._check_cancelled(registry_key)
            diff = git_diff(worktree_root, ticket.task.target_files)
            self._append_activity_event(
                ticket,
                "diff_produced",
                payload={
                    "target_files": ticket.task.target_files,
                    "diff": diff,
                    "line_count": len(diff.splitlines()),
                },
                model_id=ticket.execution.assigned_model,
            )

            if write_error is not None:
                self._append_activity_event(
                    ticket,
                    "error",
                    payload={
                        "stage": "write",
                        "error": str(write_error),
                    },
                    model_id=ticket.execution.assigned_model,
                )
                ticket = self._save_result(
                    ticket,
                    diff=diff,
                    outcome="error",
                    test_output=str(write_error),
                )
                fallback_ticket = self._fallback_after_budget_exhausted(ticket)
                if fallback_ticket is not None:
                    ticket = fallback_ticket
                    self.repository.append_log(ticket.id, str(write_error), level="error")
                    self._reset_worktree_with_event(worktree_root, ticket, reason="write_error")
                    continue
                failed = self.state_service.record_test_failure(ticket.id)
                ticket = failed.ticket
                self.repository.append_log(ticket.id, str(write_error), level="error")
                if failed.escalated:
                    self._record_retry_escalation(ticket, failed.escalated_to)
                    self._notify_intervention(ticket, "ticket_blocked")
                    return ExecutionResult(ticket=ticket, passed=False, escalated=True)
                self._record_retry(ticket, "write_error")
                self._reset_worktree_with_event(worktree_root, ticket, reason="write_error_retry")
                continue

            if is_unverified_ticket(ticket):
                output = "Machine test gate skipped (unverified manual ticket)"
                passed = True
                self._append_activity_event(
                    ticket,
                    "dod_check",
                    payload={
                        "command": "<unverified manual ticket>",
                        "status": "pass",
                        "expected": "pass",
                        "output_tail": output,
                    },
                    model_id=ticket.execution.assigned_model,
                )
            else:
                test_results = test_runner.run_ticket_tests(ticket)
                output = format_test_results(test_results)
                passed = all(result.status == "pass" for result in test_results)
                for test_result in test_results:
                    self._record_dod_check(ticket, test_result)

            if passed:
                self._save_result(ticket, diff=diff, outcome="success", test_output=output)
                pending = self.state_service.move(ticket.id, TicketStatus.DIFF_PENDING)
                self.repository.append_log(
                    ticket.id,
                    "Tests passed; diff pending human approval",
                )
                self._notify_intervention(pending.ticket, "diff_review_required")
                return ExecutionResult(ticket=pending.ticket, passed=True)

            ticket = self._save_result(
                ticket,
                diff=diff,
                outcome="test_failed",
                test_output=output,
            )
            fallback_ticket = self._fallback_after_budget_exhausted(ticket)
            if fallback_ticket is not None:
                ticket = fallback_ticket
                self._reset_worktree_with_event(worktree_root, ticket, reason="dod_failed_fallback")
                continue
            failed = self.state_service.record_test_failure(ticket.id)
            ticket = failed.ticket
            self.repository.append_log(ticket.id, "Tests failed; retry decision recorded", level="warn")
            if failed.escalated:
                self._record_retry_escalation(ticket, failed.escalated_to)
                self._notify_intervention(ticket, "ticket_blocked")
                return ExecutionResult(ticket=ticket, passed=False, escalated=True)
            self._record_retry(ticket, "dod_failed")
            self._reset_worktree_with_event(worktree_root, ticket, reason="dod_failed_retry")
            continue

    def _fallback_after_budget_exhausted(self, ticket: Ticket) -> Ticket | None:
        if self.settings_repository is None:
            return None
        if ticket.execution.attempts + 1 <= ticket.execution.retry_budget:
            return None
        next_model = next_local_fallback_model(
            ticket.execution.assigned_model,
            self.settings_repository,
        )
        if next_model is None:
            return None

        ticket_json = ticket.to_dict()
        previous_model = ticket.execution.assigned_model
        ticket_json["status"] = TicketStatus.IN_PROGRESS.value
        ticket_json["execution"]["assigned_model"] = next_model
        ticket_json["execution"]["attempts"] = 0
        metadata = ticket_json.setdefault("metadata", {})
        metadata["local_fallback_from"] = previous_model
        metadata["local_fallback_to"] = next_model
        metadata["local_fallback_reason"] = "retry_budget_exhausted"
        updated = self.repository.save(Ticket.from_dict(ticket_json))
        self._append_activity_event(
            updated,
            "escalation",
            payload={
                "reason": "retry_budget_exhausted",
                "from_model": previous_model,
                "to_model": next_model,
                "attempts": ticket.execution.attempts,
                "retry_budget": ticket.execution.retry_budget,
                "target": "fallback_model",
            },
            model_id=previous_model,
        )
        self.repository.append_log(
            ticket.id,
            f"Local retry budget exhausted for {previous_model}; falling back to {next_model}",
            level="warn",
        )
        return updated

    def _mark_run_context(
        self,
        ticket: Ticket,
        context: ActiveRunContext,
        *,
        reasoner_prompt_version: str,
    ) -> Ticket:
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["last_run_id"] = context.run_id
        metadata["last_run_started_at"] = now_iso()
        metadata["last_run_model_id"] = ticket.execution.assigned_model
        metadata["reasoner_prompt_version"] = reasoner_prompt_version
        return self.repository.save(Ticket.from_dict(ticket_json))

    def _record_execution_prompt_version(self) -> str:
        template_source = "\n\n".join(
            [
                inspect.getsource(build_file_rewrite_prompt),
                inspect.getsource(build_file_patch_prompt),
            ]
        )
        return record_prompt_version(
            self.prompt_versions,
            name="coder-execution-prompt",
            template=template_source,
        )

    def _append_activity_event(
        self,
        ticket: Ticket,
        event_type,
        *,
        payload: dict | None = None,
        model_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        cost_status: str | None = None,
    ) -> None:
        context = self._active_run_context
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        self.run_events.append_run_event(
            project_id=context.project_id if context else _ticket_project_id(ticket),
            requirement_id=context.requirement_id if context else _ticket_requirement_id(ticket),
            ticket_id=context.ticket_id if context else ticket.id,
            run_id=context.run_id if context else _string_or_none(metadata.get("last_run_id")),
            event_type=event_type,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cost_status=cost_status,
            payload=payload,
        )

    def _record_model_call(
        self,
        ticket: Ticket,
        *,
        prompt: str,
        response: str,
        target_file: str,
        edit_mode: str,
        max_tokens: int,
        usage: CloudUsage,
    ) -> None:
        if usage.total_tokens:
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cost_usd = usage.cost_usd
            cost_status = usage.cost_status
        else:
            input_tokens = estimate_tokens(prompt)
            output_tokens = estimate_tokens(response)
            cost_usd = 0.0
            cost_status = "unknown"
        self._append_activity_event(
            ticket,
            "model_call",
            model_id=ticket.execution.assigned_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cost_status=cost_status,
            payload={
                "target_file": target_file,
                "edit_mode": edit_mode,
                "max_tokens": max_tokens,
                "used_cloud_usage": usage.total_tokens > 0,
                "reasoner_prompt_version": self._current_prompt_version(ticket),
            },
        )

    def _record_dod_check(self, ticket: Ticket, result: TestRunResult) -> None:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        self._append_activity_event(
            ticket,
            "dod_check",
            payload={
                "command": result.command,
                "status": result.status,
                "expected": result.expect,
                "return_code": result.return_code,
                "timed_out": result.timed_out,
                "output_tail": _tail(output),
            },
            model_id=ticket.execution.assigned_model,
        )

    def _record_retry(self, ticket: Ticket, reason: str) -> None:
        self._append_activity_event(
            ticket,
            "retry",
            payload={
                "attempt": ticket.execution.attempts,
                "retry_budget": ticket.execution.retry_budget,
                "reason": reason,
            },
            model_id=ticket.execution.assigned_model,
        )

    def _record_retry_escalation(self, ticket: Ticket, escalated_to: str | None) -> None:
        self._append_activity_event(
            ticket,
            "escalation",
            payload={
                "reason": "retry_budget_exhausted",
                "attempts": ticket.execution.attempts,
                "retry_budget": ticket.execution.retry_budget,
                "escalated_to": escalated_to or ticket.execution.escalate_to,
            },
            model_id=ticket.execution.assigned_model,
        )

    def _record_cloud_execution_usage(self, ticket: Ticket, execution_client: object) -> Ticket:
        usage = getattr(execution_client, "last_usage", None)
        if not isinstance(usage, CloudUsage) or usage.total_tokens == 0:
            return ticket

        payload = ticket.to_dict()
        metadata = payload.setdefault("metadata", {})
        metadata["cloud_input_tokens"] = int(metadata.get("cloud_input_tokens") or 0) + usage.input_tokens
        metadata["cloud_output_tokens"] = int(metadata.get("cloud_output_tokens") or 0) + usage.output_tokens
        metadata["cloud_cost_usd"] = round(
            float(metadata.get("cloud_cost_usd") or 0.0) + usage.cost_usd,
            4,
        )
        metadata["cloud_cost_status"] = usage.cost_status
        updated = self.repository.save(Ticket.from_dict(payload))
        self.repository.append_log(
            ticket.id,
            (
                "Cloud execution usage recorded: "
                f"{usage.input_tokens} input tokens, {usage.output_tokens} output tokens, "
                f"${usage.cost_usd:.4f}"
            ),
        )

        requirement_id = metadata.get("requirement_id")
        if self.requirement_repository is not None and isinstance(requirement_id, str):
            requirement = self.requirement_repository.get(requirement_id)
            if requirement is not None:
                self.requirement_repository.save(apply_usage_to_requirement(requirement, usage))

        return updated

    def _notify_intervention(self, ticket: Ticket, reason: str) -> None:
        NotificationService(self.repository, self.settings_repository).notify_intervention_needed(
            ticket,
            reason,
        )

    def _prepare_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        status = TicketStatus(ticket.status)
        if status == TicketStatus.BACKLOG:
            ticket = self.state_service.move(ticket.id, TicketStatus.READY).ticket
            status = TicketStatus(ticket.status)
        if status == TicketStatus.READY:
            ticket = self.state_service.move(ticket.id, TicketStatus.IN_PROGRESS).ticket
            status = TicketStatus(ticket.status)
        if status != TicketStatus.IN_PROGRESS:
            raise InvalidTransitionError(
                f"Ticket must be backlog, ready, or in_progress to execute; got {status.value}"
            )
        return ticket

    def _save_result(self, ticket: Ticket, *, diff: str, outcome: str, test_output: str) -> Ticket:
        ticket_json = ticket.to_dict()
        existing_logs = ticket_json.get("result", {}).get("logs", [])
        extra_secrets = (
            current_known_secrets(get_settings(), self.settings_repository)
            if self.settings_repository is not None
            else None
        )
        ticket_json["result"] = Result(
            outcome=outcome,
            diff=redact_text(diff, extra_secrets=extra_secrets),
            diff_stats=derive_diff_stats(diff, ticket.task.target_files),
            test_output=redact_text(test_output, extra_secrets=extra_secrets),
            logs=existing_logs,
        ).model_dump(mode="json", exclude_none=True)
        return self.repository.save(Ticket.from_dict(ticket_json))

    def _reset_worktree_with_event(self, worktree_root: Path, ticket: Ticket, *, reason: str) -> None:
        was_dirty = _is_git_dirty(worktree_root)
        self.workspace_guard.reset_worktree(worktree_root)
        if not was_dirty:
            return
        self._append_activity_event(
            ticket,
            "rollback",
            payload={
                "reason": reason,
                "detail": f"Rolled back dirty ticket worktree before {reason}",
            },
            model_id=ticket.execution.assigned_model,
        )

    def _current_prompt_version(self, ticket: Ticket) -> str | None:
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        value = metadata.get("reasoner_prompt_version")
        return value if isinstance(value, str) else None

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _test_runner_for(self, cwd: Path) -> TestRunner:
        if self.test_runner is None or type(self.test_runner) is TestRunner:
            if self.test_runner is None:
                return TestRunner(cwd=cwd, audit_sink=self._record_sandbox_audit)
            return TestRunner(
                cwd=cwd,
                env=self.test_runner.env,
                setup_cmd=self.test_runner.setup_cmd,
                cleanup_cmd=self.test_runner.cleanup_cmd,
                setup_timeout_sec=self.test_runner.setup_timeout_sec,
                cleanup_timeout_sec=self.test_runner.cleanup_timeout_sec,
                execution_policy=self.test_runner.execution_policy,
                audit_sink=self._record_sandbox_audit,
            )
        return self.test_runner

    def _record_sandbox_audit(self, audit: SandboxAudit) -> None:
        context = self._active_run_context
        project_id = context.project_id if context else self.repository.project_id or "default"
        ticket_id = context.ticket_id if context else None
        requirement_id = context.requirement_id if context else None
        run_id = context.run_id if context else None
        payload = {
            "stage": "sandbox",
            "kind": audit.event_type,
            "reason": audit.reason,
            "command": audit.command,
            "primitive": audit.primitive,
            "blocked": audit.blocked,
            "message": audit.message,
            "detail": audit.message or audit.reason or audit.command,
        }
        if audit.event_type == "egress_attempt":
            payload["destination"] = "network"
        self.run_events.append_run_event(
            project_id=project_id,
            requirement_id=requirement_id,
            ticket_id=ticket_id,
            run_id=run_id,
            event_type=audit.event_type,
            payload=payload,
        )


def build_file_rewrite_prompt(ticket: Ticket, target_file: str) -> str:
    context = "\n\n".join(
        f"File: {file.path}\n```\n{file.content}\n```" for file in ticket.context.files
    )
    tests = "\n".join(
        f"- {test.command} (expect {test.expect}, timeout {test.timeout_sec}s)"
        for test in ticket.definition_of_done.tests
    )
    previous_output = ""
    if ticket.result and ticket.result.test_output:
        previous_output = f"\nPrevious test output:\n{ticket.result.test_output}\n"

    return redact_text(
        "You are the local coder model for HAAO. Rewrite exactly one file.\n"
        f"SECURITY: {UNTRUSTED_CONTEXT_INSTRUCTION}\n"
        "Return the complete updated file content for the requested target file only.\n"
        "Do not include markdown fences, explanations, comments about the change, "
        "diff markers, or file path headers.\n\n"
        f"Ticket {ticket.id}: {ticket.title}\n"
        f"Task:\n{ticket.task.description}\n"
        f"{_rework_guidance(ticket)}\n"
        f"Allowed target files:\n{', '.join(ticket.task.target_files)}\n\n"
        f"Requested target file:\n{target_file}\n\n"
        f"Constraints:\n" + "\n".join(f"- {item}" for item in ticket.task.constraints) + "\n\n"
        f"Context:\n{context}\n\n"
        f"Definition of done:\n{tests}\n"
        f"{previous_output}"
    )


def build_file_patch_prompt(ticket: Ticket, target_file: str) -> str:
    context = "\n\n".join(
        f"File: {file.path}\n```\n{file.content}\n```" for file in ticket.context.files
    )
    tests = "\n".join(
        f"- {test.command} (expect {test.expect}, timeout {test.timeout_sec}s)"
        for test in ticket.definition_of_done.tests
    )
    previous_output = ""
    if ticket.result and ticket.result.test_output:
        previous_output = f"\nPrevious test output:\n{ticket.result.test_output}\n"

    return redact_text(
        "You are the local coder model for HAAO. Edit exactly one file using "
        "SEARCH/REPLACE blocks.\n"
        f"SECURITY: {UNTRUSTED_CONTEXT_INSTRUCTION}\n"
        "Return one or more blocks in exactly this format:\n\n"
        "<<<<<<< SEARCH\n"
        "<exact existing text to replace>\n"
        "=======\n"
        "<replacement text>\n"
        ">>>>>>> REPLACE\n\n"
        "Every SEARCH section must match the current file content character-for-character "
        "and occur exactly once. Keep each block as small as possible and include only the "
        "part that must change — never replace a whole function or file with a stub, and "
        "never delete large regions. You may return multiple blocks. Do not return the "
        "complete file, a unified diff, line numbers, markdown fences, file headers, "
        "explanations, or any text outside the blocks.\n\n"
        "Example:\n"
        "<<<<<<< SEARCH\n"
        "    return value\n"
        "=======\n"
        "    return value + 1\n"
        ">>>>>>> REPLACE\n\n"
        f"Ticket {ticket.id}: {ticket.title}\n"
        f"Task:\n{ticket.task.description}\n"
        f"{_rework_guidance(ticket)}\n"
        f"Allowed target files:\n{', '.join(ticket.task.target_files)}\n\n"
        f"Requested target file:\n{target_file}\n\n"
        f"Constraints:\n" + "\n".join(f"- {item}" for item in ticket.task.constraints) + "\n\n"
        f"Context:\n{context}\n\n"
        f"Definition of done:\n{tests}\n"
        f"{previous_output}"
    )


def apply_search_replace_blocks(file_path: str | Path, model_output: str) -> None:
    blocks = _parse_search_replace_blocks(model_output)
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    updated = content
    for block_index, (search, replacement) in enumerate(blocks, start=1):
        match_count = _count_occurrences(updated, search)
        if match_count != 1:
            reason = "not_found" if match_count == 0 else "ambiguous"
            raise SearchReplaceBlockError(block_index, reason, match_count)
        current_lines = updated.count("\n") + 1
        search_lines = search.count("\n") + 1
        if (
            current_lines >= DESTRUCTIVE_MIN_LINES
            and search_lines >= DESTRUCTIVE_BLOCK_COVERAGE * current_lines
            and len(replacement) < 0.5 * len(search)
        ):
            raise DestructiveEditError(
                f"SEARCH block {block_index} spans {search_lines} of {current_lines} lines "
                f"and the replacement is far smaller"
            )
        updated = updated.replace(search, replacement, 1)

    # Reject net deletions before touching disk so a bad patch never corrupts the file.
    if (
        len(content) >= DESTRUCTIVE_MIN_CHARS
        and len(updated) < (1 - DESTRUCTIVE_MAX_SHRINK_RATIO) * len(content)
    ):
        raise DestructiveEditError(
            f"result is {len(updated)} chars, down from {len(content)} "
            f"(more than {int(DESTRUCTIVE_MAX_SHRINK_RATIO * 100)}% smaller)"
        )

    path.write_text(updated, encoding="utf-8")


def _count_occurrences(content: str, search: str) -> int:
    count = 0
    start = 0
    while True:
        match = content.find(search, start)
        if match < 0:
            return count
        count += 1
        start = match + 1


def _is_search_marker(line: str) -> bool:
    s = line.strip()
    return s.startswith("<<<<<<<") and "SEARCH" in s


def _is_divider(line: str) -> bool:
    return line.strip() == "======="


def _is_replace_marker(line: str) -> bool:
    s = line.strip()
    return s.startswith(">>>>>>>") and "REPLACE" in s


def _parse_search_replace_blocks(model_output: str) -> list[tuple[str, str]]:
    lines = model_output.splitlines()
    blocks: list[tuple[str, str]] = []
    index = 0
    n = len(lines)

    while index < n:
        # Tolerate any noise around blocks (blank lines, prose, code fences, file
        # headers); only the block internals below are parsed strictly.
        if not _is_search_marker(lines[index]):
            index += 1
            continue

        block_index = len(blocks) + 1
        index += 1
        search_start = index
        while index < n and not _is_divider(lines[index]):
            index += 1
        if index == n:
            raise SearchReplaceParseError(
                f"SEARCH block {block_index} is missing the ======= separator"
            )
        search = "\n".join(lines[search_start:index])
        if not search:
            raise SearchReplaceParseError(f"SEARCH block {block_index} is empty")

        index += 1
        replacement_start = index
        while index < n and not _is_replace_marker(lines[index]):
            index += 1
        if index == n:
            raise SearchReplaceParseError(
                f"SEARCH block {block_index} is missing the >>>>>>> REPLACE marker"
            )
        replacement = "\n".join(lines[replacement_start:index])
        blocks.append((search, replacement))
        index += 1

    if not blocks:
        raise SearchReplaceParseError("Model output contained no SEARCH/REPLACE blocks")
    return blocks


def required_rewrite_output_tokens(content: str) -> int:
    """Estimate the output budget needed to rewrite one complete target file."""
    return ceil(estimate_tokens(content) * OUTPUT_TOKEN_MULTIPLIER) + OUTPUT_TOKEN_OVERHEAD


def _target_file_content(ticket: Ticket, target_file: str) -> str:
    normalized = normalize_repo_path(target_file)
    for context_file in ticket.context.files:
        if normalize_repo_path(context_file.path) == normalized:
            return context_file.content
    return ""


def _ticket_project_id(ticket: Ticket) -> str:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    project_id = metadata.get("project_id")
    return project_id if isinstance(project_id, str) and project_id else "default"


def _ticket_requirement_id(ticket: Ticket) -> str | None:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    requirement_id = metadata.get("requirement_id")
    return requirement_id if isinstance(requirement_id, str) and requirement_id else None


def _supports_max_tokens(local_model: LocalModel) -> bool:
    """Keep legacy/in-test adapters working while capping real LM Studio calls."""
    parameters = inspect.signature(local_model.chat_completion).parameters.values()
    return any(
        parameter.name == "max_tokens"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _refresh_target_context(
    ticket: Ticket,
    worktree_root: Path,
    *,
    target_files: list[str] | None = None,
) -> Ticket:
    """Return an in-memory ticket whose target snapshots match the worktree.

    Context refreshes are deliberately not persisted: the database retains the
    original dispatch snapshot, while one execution attempt gets a coherent view
    of files written earlier in that same attempt.
    """
    root = worktree_root.resolve()
    payload = ticket.to_dict()
    context = payload["context"]
    snapshots = context.setdefault("files", [])
    by_path = {
        normalize_repo_path(snapshot["path"]): snapshot
        for snapshot in snapshots
    }

    for raw_path in target_files or ticket.task.target_files:
        normalized = normalize_repo_path(raw_path)
        source = (root / normalized).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            continue
        content = source.read_text(encoding="utf-8")
        snapshot = by_path.get(normalized)
        if snapshot is None:
            snapshot = {
                "path": normalized,
                "content": content,
                "reason": "Current target file from the active worktree",
            }
            snapshots.append(snapshot)
            by_path[normalized] = snapshot
        else:
            snapshot["content"] = content
            snapshot["truncated"] = False

    # The original estimate no longer describes refreshed/generated contents.
    context.pop("token_estimate", None)
    return Ticket.from_dict(payload)


def _rework_guidance(ticket: Ticket) -> str:
    """Feed prior review/audit rejection feedback back into the coder prompt (A6).

    Without this, a rejected ticket is re-dispatched with an identical prompt, so
    the model produces the same file, the audit rejects it again ("diff identical
    to previously rejected diff"), and the rework loop never converges.
    """
    notes: list[str] = []

    audit = ticket.audit
    if audit is not None and getattr(audit, "verdict", "") == "rejected":
        feedback = (getattr(audit, "feedback", "") or "").strip()
        if feedback:
            notes.append(feedback)

    meta = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    for key in ("diff_rejection_feedback", "product_rejection_feedback"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            notes.append(value.strip())
    value = meta.get("previous_review_feedback")
    if isinstance(value, str) and value.strip():
        notes.append(value.strip())

    if not notes:
        return ""

    joined = "\n".join(f"- {note}" for note in notes)
    previous_diff = ticket.result.diff if ticket.result and ticket.result.diff else ""
    if not previous_diff.strip():
        meta = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        previous_diff = meta.get("previous_rejected_diff", "")
    diff_block = ""
    if previous_diff.strip():
        diff_block = (
            "\nYour previous diff was REJECTED — do not resubmit it unchanged:\n"
            f"{previous_diff[:4000]}\n"
        )

    return (
        "\n*** REWORK REQUIRED ***\n"
        "Your previous attempt was rejected in review. You MUST fix every point "
        "below and return a corrected file; resubmitting the same content will be "
        "rejected again:\n"
        f"{joined}\n"
        f"{diff_block}"
    )


def strip_code_fence(model_output: str) -> str:
    probe = model_output.strip()
    lines = probe.splitlines()
    stripped_prefix = False

    if lines and re.fullmatch(
        r"(?:[\w.@+-]+[/\\])+[\w.@+-]+\.[A-Za-z0-9]+",
        lines[0].strip(),
    ):
        lines = lines[1:]
        stripped_prefix = True

    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        stripped_prefix = True
    if not stripped_prefix:
        return model_output

    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def git_diff(repo_root: Path, target_files: list[str]) -> str:
    completed = subprocess.run(
        ["git", "diff", "--", *target_files],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return completed.stderr.strip()
    return completed.stdout


def _is_git_dirty(repo_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return False
    return bool(completed.stdout.strip())


def format_test_results(results: list[TestRunResult]) -> str:
    chunks: list[str] = []
    for result in results:
        chunks.append(
            "\n".join(
                [
                    f"$ {result.command}",
                    f"status={result.status} expected={result.expect}",
                    f"return_code={result.return_code}",
                    "stdout:",
                    result.stdout,
                    "stderr:",
                    result.stderr,
                ]
            )
        )
    return "\n\n".join(chunks)


def _usage_from_client(execution_client: object) -> CloudUsage:
    usage = getattr(execution_client, "last_usage", None)
    return usage if isinstance(usage, CloudUsage) else CloudUsage()


def _tail(value: str, *, max_chars: int = 4000) -> str:
    return value[-max_chars:] if len(value) > max_chars else value


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
