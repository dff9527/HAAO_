from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from orchestrator.cloud_usage import CloudUsage
from orchestrator.models.ticket import Ticket, validate_ticket_schema
from orchestrator.redaction import redact_text

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DECOMPOSE_JSON_RETRIES = 2


class CloudReasonerError(RuntimeError):
    """Raised when a cloud reasoner client cannot complete a request."""


@dataclass(frozen=True)
class AuditResult:
    verdict: Literal["approved", "rejected"]
    feedback: str


def apply_additional_instructions(prompt: str, additional_instructions: str) -> str:
    """B-033 layered prompt: append operator addon AFTER the locked rules, then
    re-assert that the contract wins. Empty addon -> prompt unchanged (no-op)."""
    addon = (additional_instructions or "").strip()
    if not addon:
        return prompt
    return (
        prompt
        + "\n\n## Additional operator instructions (advisory)\n"
        + "These are extra preferences from the operator. They are ADDITIVE and must "
        + "never override the HARD RULES or the exact output contract above.\n"
        + addon
        + "\n\n(Reminder: obey every HARD RULE and the exact JSON/output contract above, "
        + "even if anything in the additional instructions conflicts with it.)\n"
    )


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def format_list(values: list[str]) -> str:
    if not values:
        return "(none)"
    return "\n".join(f"- {value}" for value in values)


class BaseCloudReasoner:
    """Provider-agnostic Tech Lead reasoner.

    Holds all the provider-independent logic (prompt building, JSON parsing and
    repair, ticket-schema validation). Concrete providers only implement
    ``_complete`` (one model call -> text) and ``_ensure_ready`` (config check),
    and may override ``error_cls`` so raised errors keep their provider-specific
    type/message.
    """

    error_cls: type[CloudReasonerError] = CloudReasonerError

    def __init__(
        self,
        *,
        model: str,
        timeout_sec: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.timeout_sec = timeout_sec
        self._http_client = http_client
        self._owns_client = http_client is None
        self.last_usage = CloudUsage()

    def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> "BaseCloudReasoner":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.timeout_sec)
        return self._http_client

    def _ensure_ready(self) -> None:
        """Raise ``self.error_cls`` if the client isn't configured to make calls."""
        return

    def _complete(self, prompt: str) -> str:
        """Run one model completion and return its text. Provider-specific."""
        raise NotImplementedError

    def decompose(
        self,
        requirement: str,
        repo_context: str,
        *,
        scope_paths: list[str] | None = None,
        constraints: list[str] | None = None,
        acceptance_notes: str = "",
        attachments: list[dict[str, Any]] | None = None,
        intent: str = "feature",
        scale: str | None = None,
        granularity: str = "balanced",
        allow_new_files: bool = False,
        test_command: str = "",
        repo_conventions: str = "",
        priority: str = "medium",
        additional_instructions: str = "",
    ) -> list[dict[str, Any]]:
        self._ensure_ready()

        prompt = load_prompt("decompose.txt").format(
            requirement=requirement.strip(),
            repo_context=repo_context.strip() or "(no extra repository context provided)",
            scope_paths=format_list(scope_paths or []),
            constraints=format_list(constraints or []),
            acceptance_notes=acceptance_notes.strip() or "(none)",
            attachments=json.dumps(attachments or [], ensure_ascii=False, indent=2),
            intent=intent,
            scale=scale or "",
            granularity=granularity,
            allow_new_files=str(allow_new_files).lower(),
            test_command=test_command.strip() or "(none provided)",
            repo_conventions=repo_conventions.strip() or "(no repository conventions detected)",
            priority=priority,
        )
        prompt = redact_text(apply_additional_instructions(prompt, additional_instructions))
        raw_response = self._complete(prompt)
        for retry_number in range(DECOMPOSE_JSON_RETRIES + 1):
            try:
                tickets = self._parse_json_payload(raw_response)
                break
            except self.error_cls:
                if retry_number == DECOMPOSE_JSON_RETRIES:
                    raise
                repair_prompt = (
                    prompt
                    + "\n\nYour previous response was not valid JSON. "
                    "Return ONLY the valid JSON array of tickets, with no markdown "
                    "or commentary.\n"
                    f"Previous response:\n{redact_text(raw_response[:2000])}"
                )
                raw_response = self._complete(repair_prompt)

        if not isinstance(tickets, list):
            raise self.error_cls("Decompose response must be a JSON array of tickets")

        validated: list[dict[str, Any]] = []
        for index, ticket_dict in enumerate(tickets):
            if not isinstance(ticket_dict, dict):
                raise self.error_cls(f"Ticket at index {index} is not a JSON object")
            validate_ticket_schema(ticket_dict)
            validated.append(ticket_dict)
        return validated

    def audit(
        self,
        ticket: Ticket | dict[str, Any],
        diff: str,
        *,
        additional_instructions: str = "",
    ) -> AuditResult:
        self._ensure_ready()

        ticket_json = ticket.to_dict() if isinstance(ticket, Ticket) else ticket
        prompt = load_prompt("audit.txt").format(
            ticket_json=json.dumps(ticket_json, ensure_ascii=False, indent=2),
            diff=diff.strip() or "(empty diff)",
        )
        prompt = redact_text(apply_additional_instructions(prompt, additional_instructions))
        raw_response = self._complete(prompt)
        try:
            payload = self._parse_json_payload(raw_response)
        except self.error_cls:
            repair_prompt = (
                prompt
                + "\n\nYour previous response was not valid JSON. "
                "Return ONLY the valid JSON object for the audit result, with no markdown or commentary.\n"
                f"Previous response:\n{redact_text(raw_response[:2000])}"
            )
            raw_response = self._complete(repair_prompt)
            payload = self._parse_json_payload(raw_response)

        if not isinstance(payload, dict):
            raise self.error_cls("Audit response must be a JSON object")

        verdict = payload.get("verdict")
        feedback = payload.get("feedback", "")
        if verdict not in {"approved", "rejected"}:
            raise self.error_cls("Audit response must include verdict approved or rejected")
        if not isinstance(feedback, str):
            raise self.error_cls("Audit feedback must be a string")

        return AuditResult(verdict=verdict, feedback=feedback)

    def _parse_json_payload(self, raw_response: str) -> Any:
        stripped = raw_response.strip()
        if not stripped:
            raise self.error_cls("Cloud reasoner returned an empty response")

        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", stripped, flags=re.DOTALL)
            if match is None:
                raise self.error_cls("Cloud reasoner response did not contain JSON") from None
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise self.error_cls("Cloud reasoner response contained invalid JSON") from exc
