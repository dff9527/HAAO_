from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from orchestrator.db.sqlite import SettingsRepository, TicketRepository
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
        self._send_webhook(notification)
        return notification

    def _send_webhook(self, notification: InterventionNotification) -> None:
        if self.settings_repository is None:
            return
        webhook_url = self.settings_repository.get_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, "")
        if not isinstance(webhook_url, str) or not webhook_url.strip():
            return
        try:
            httpx.post(webhook_url, json=notification.to_dict(), timeout=5.0)
        except httpx.HTTPError:
            self.repository.append_log(
                notification.ticket_id,
                "Intervention webhook notification failed",
                level="warn",
            )


def get_notification_webhook(repository: SettingsRepository) -> str:
    value = repository.get_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, "")
    return value if isinstance(value, str) else ""


def set_notification_webhook(repository: SettingsRepository, webhook_url: str) -> str:
    cleaned = webhook_url.strip()
    repository.set_json(NOTIFICATION_WEBHOOK_SETTINGS_KEY, cleaned)
    return cleaned
