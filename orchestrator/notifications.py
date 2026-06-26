from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from orchestrator.db.sqlite import ChatRepository, RunEventRepository, SettingsRepository, TicketRepository
from orchestrator.integration_notifications import post_slack_integration, slack_blocked_payload
from orchestrator.models.ticket import Ticket

NOTIFICATION_WEBHOOK_SETTINGS_KEY = "notification_webhook_url"


@dataclass(frozen=True)
class InterventionNotification:
    ticket_id: str
    status: str
    reason: str
    ticket_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ticket_id": self.ticket_id,
            "status": self.status,
            "reason": self.reason,
            "ticket_url": self.ticket_url,
        }


class NotificationService:
    def __init__(
        self,
        repository: TicketRepository,
        settings_repository: SettingsRepository | None = None,
    ) -> None:
        self.repository = repository
        self.settings_repository = settings_repository

    def notify_intervention_needed(self, ticket: Ticket, reason: str) -> InterventionNotification:
        notification = InterventionNotification(
            ticket_id=ticket.id,
            status=str(ticket.status),
            reason=reason,
            ticket_url=f"/tickets/{ticket.id}",
        )
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["last_intervention_notification"] = {
            **notification.to_dict(),
            "sent_at": datetime.now(UTC).isoformat(),
        }
        updated = self.repository.save(Ticket.from_dict(ticket_json))
        self.repository.append_log(
            updated.id,
            f"Intervention needed: {reason} ({notification.ticket_url})",
            level="warn",
        )
        self._append_chat_report(updated, reason)
        self._append_run_report(updated, reason, notification)
        self._send_webhook(notification)
        return notification

    def _send_webhook(self, notification: InterventionNotification) -> None:
        if self.settings_repository is not None:
            webhook_url = self.settings_repository.get_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, "")
            if isinstance(webhook_url, str) and webhook_url.strip():
                try:
                    httpx.post(webhook_url, json=notification.to_dict(), timeout=5.0)
                except httpx.HTTPError:
                    self.repository.append_log(
                        notification.ticket_id,
                        "Intervention webhook notification failed",
                        level="warn",
                    )
        ticket = self.repository.get(notification.ticket_id)
        project_id = _ticket_project_id(ticket) if ticket is not None else "default"
        title = ticket.title if ticket is not None else notification.ticket_id
        post_slack_integration(
            self.repository.connection,
            project_id=project_id,
            ticket_id=notification.ticket_id,
            run_id=_string_or_none(
                ticket.metadata.model_dump(mode="json").get("last_run_id")
                if ticket is not None and ticket.metadata is not None
                else None
            ),
            payload=slack_blocked_payload(
                ticket_id=notification.ticket_id,
                title=title,
                status=notification.status,
                reason=notification.reason,
                ticket_url=notification.ticket_url,
            ),
        )

    def _append_chat_report(self, ticket: Ticket, reason: str) -> None:
        project_id = _ticket_project_id(ticket)
        chat_repository = ChatRepository(self.repository.connection)
        segment_id = chat_repository.active_segment_id(project_id)
        report_kind = _report_kind(ticket, reason)
        label = ticket.title or ticket.id
        if report_kind == "blocked":
            text = f"{ticket.id} blocked - needs a decision: {reason}"
        else:
            text = f"{ticket.id} needs you - {reason}: {label}"
        chat_repository.append_message(
            project_id=project_id,
            role="system_report",
            text=text,
            segment_id=segment_id,
            ticket_id=ticket.id,
            report_kind=report_kind,
        )

    def _append_run_report(
        self,
        ticket: Ticket,
        reason: str,
        notification: InterventionNotification,
    ) -> None:
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        RunEventRepository(self.repository.connection).append_run_event(
            project_id=_ticket_project_id(ticket),
            requirement_id=_ticket_requirement_id(ticket),
            ticket_id=ticket.id,
            run_id=_string_or_none(metadata.get("last_run_id")),
            event_type="report",
            payload={
                "report_kind": _report_kind(ticket, reason),
                "reason": reason,
                "status": notification.status,
                "ticket_url": notification.ticket_url,
            },
        )


def get_notification_webhook(repository: SettingsRepository) -> str:
    value = repository.get_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, "")
    return value if isinstance(value, str) else ""


def set_notification_webhook(repository: SettingsRepository, webhook_url: str) -> str:
    cleaned = webhook_url.strip()
    repository.set_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, cleaned)
    return cleaned


def _report_kind(ticket: Ticket, reason: str) -> str:
    if str(ticket.status) == "blocked" or "blocked" in reason.lower():
        return "blocked"
    return "needs_you"


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


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
