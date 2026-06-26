from __future__ import annotations

import sqlite3
from typing import Any

import httpx

from orchestrator.db.sqlite import IntegrationRepository, RunEventRepository
from orchestrator.redaction import redact_text


def post_slack_integration(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    ticket_id: str | None,
    payload: dict[str, Any],
    run_id: str | None = None,
    retries: int = 2,
) -> bool:
    """Post to configured Slack webhook integrations.

    Slack credentials are stored in the generic integration store. For Wave 1a the
    token value is the incoming webhook URL, matching the existing webhook sender.
    """
    integrations = IntegrationRepository(connection)
    credentials = integrations.list("slack")
    if not credentials:
        return False

    sent = False
    events = RunEventRepository(connection)
    for credential in credentials:
        webhook_url = ""
        try:
            webhook_url = integrations.decrypted_token("slack", credential.id)
            _post_with_retry(webhook_url, payload, retries=retries)
            sent = True
        except Exception as exc:  # noqa: BLE001 - notification failures are non-fatal.
            events.append_run_event(
                project_id=project_id,
                ticket_id=ticket_id,
                run_id=run_id,
                event_type="error",
                payload={
                    "stage": "slack_notification",
                    "provider": "slack",
                    "error": redact_text(
                        str(exc),
                        extra_secrets=[webhook_url] if webhook_url else None,
                    ),
                },
            )
    return sent


def _post_with_retry(webhook_url: str, payload: dict[str, Any], *, retries: int) -> None:
    last_exc: Exception | None = None
    for _ in range(max(1, retries + 1)):
        try:
            response = httpx.post(webhook_url, json=payload, timeout=5.0)
            response.raise_for_status()
            return
        except httpx.HTTPError as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc


def slack_pr_payload(
    *,
    ticket_id: str,
    title: str,
    status: str,
    pr_url: str,
) -> dict[str, Any]:
    return {
        "text": f"{ticket_id} PR {status}: {title} - {pr_url}",
        "ticket_id": ticket_id,
        "status": status,
        "pr_url": pr_url,
    }


def slack_blocked_payload(
    *,
    ticket_id: str,
    title: str,
    status: str,
    reason: str,
    ticket_url: str,
) -> dict[str, Any]:
    return {
        "text": f"{ticket_id} blocked: {title} - {reason}",
        "ticket_id": ticket_id,
        "status": status,
        "reason": reason,
        "ticket_url": ticket_url,
    }
