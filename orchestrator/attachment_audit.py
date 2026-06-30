from __future__ import annotations

import sqlite3
from typing import Any

from clients.factory import split_provider
from orchestrator.db.sqlite import RunEventRepository


def record_attachment_egress(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    attachment_id: str,
    provider: str,
    model: str,
    requirement_id: str | None = None,
    ticket_id: str | None = None,
    chat_message_id: str | None = None,
    run_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "kind": "attachment_egress",
        "attachment_id": attachment_id,
        "provider": provider,
        "model": model,
        "requirement_id": requirement_id,
        "ticket_id": ticket_id,
        "chat_message_id": chat_message_id,
    }
    if extra:
        payload.update(extra)
    RunEventRepository(connection).append_run_event(
        project_id=project_id,
        requirement_id=requirement_id,
        ticket_id=ticket_id,
        run_id=run_id,
        event_type="attachment_egress",
        model_id=model,
        payload=payload,
    )


def cloud_destination_from_reasoner(reasoner: object) -> tuple[str, str]:
    model = str(getattr(reasoner, "model", "") or "")
    provider = str(getattr(reasoner, "provider", "") or "")
    if not provider:
        provider, _ = split_provider(model)
    return provider or "unknown", model or "unknown"
