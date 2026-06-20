from __future__ import annotations

import threading
from pathlib import Path


class ExecutionCancelledError(RuntimeError):
    """Raised when a ticket execution is cancelled by the user."""


class ExecutionRegistry:
    """Tracks in-flight ticket executions for cooperative cancellation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._worktrees: dict[str, Path] = {}

    def register(self, ticket_id: str) -> threading.Event:
        with self._lock:
            cancel_event = threading.Event()
            self._cancel_events[ticket_id] = cancel_event
            return cancel_event

    def unregister(self, ticket_id: str) -> None:
        with self._lock:
            self._cancel_events.pop(ticket_id, None)
            self._worktrees.pop(ticket_id, None)

    def is_registered(self, ticket_id: str) -> bool:
        with self._lock:
            return ticket_id in self._cancel_events

    def request_cancel(self, ticket_id: str) -> bool:
        with self._lock:
            cancel_event = self._cancel_events.get(ticket_id)
            if cancel_event is None:
                return False
            cancel_event.set()
            return True

    def is_cancelled(self, ticket_id: str) -> bool:
        with self._lock:
            cancel_event = self._cancel_events.get(ticket_id)
            return cancel_event.is_set() if cancel_event is not None else False

    def set_worktree(self, ticket_id: str, worktree_path: Path) -> None:
        with self._lock:
            self._worktrees[ticket_id] = worktree_path

    def get_worktree(self, ticket_id: str) -> Path | None:
        with self._lock:
            return self._worktrees.get(ticket_id)


def execution_key(project_id: str | None, ticket_id: str) -> str:
    return f"{project_id or 'default'}:{ticket_id}"


execution_registry = ExecutionRegistry()
