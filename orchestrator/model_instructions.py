from __future__ import annotations

import inspect
from typing import Any, Protocol

from orchestrator.db.sqlite import SettingsRepository
from orchestrator.role_routing import DEFAULT_ROLE_ROUTING

MODEL_ID_ALIASES = {
    "Claude · Tech Lead": "claude-tech-lead",
    "Claude PO": "claude-tech-lead",
}


def normalize_model_settings_id(model_id: str) -> str:
    stripped = model_id.strip()
    return MODEL_ID_ALIASES.get(stripped, stripped)


def tech_lead_additional_instructions(
    settings_repository: SettingsRepository | None,
) -> str:
    if settings_repository is None:
        return ""
    routing = settings_repository.get_role_routing(DEFAULT_ROLE_ROUTING)
    model_id = routing.get("tech_lead", DEFAULT_ROLE_ROUTING["tech_lead"])
    return settings_repository.get_model_addon(model_id)


class _Auditor(Protocol):
    def audit(self, ticket: Any, diff: str, **kwargs: Any) -> Any:
        ...


def call_auditor(
    auditor: _Auditor,
    ticket: Any,
    diff: str,
    *,
    additional_instructions: str = "",
) -> Any:
    signature = inspect.signature(auditor.audit)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        accepted_kwargs = {"additional_instructions": additional_instructions}
    else:
        accepted_kwargs = {}
        if "additional_instructions" in signature.parameters:
            accepted_kwargs["additional_instructions"] = additional_instructions
    return auditor.audit(ticket, diff, **accepted_kwargs)
