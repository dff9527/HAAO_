"""Conversational orchestrator agent — chat session layer.

This is the judgment-heavy core of the conversational front-end (design:
``HAAO_chat_agent_design.md``, plan: ``HAAO_chat_agent_impl_plan.md``).

Responsibilities owned here:
  * Hold conversation state per (project, segment).
  * Decide when a chat turn becomes work — the agent **restates intent** and only
    then files proposals; it never silently creates tickets.
  * File committed work as **BACKLOG-only** proposals via ``RequirementGateway``
    (reusing ``RequirementService.decompose_preview``), preserving the human gate.
  * Feed the reasoner a **rolling window + summary**, not the full thread — with a
    smaller window when the active reasoner is a local model.

Storage (``ChatRepository``) and the concrete reasoner are injected as Protocols so
this module is decoupled and unit-testable. Codex implements ``ChatRepository``
against the ``chat_messages`` / ``chat_segments`` tables; the reasoner is backed by
the existing cloud/local model clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from orchestrator.attachments import build_attachment_context

Role = Literal["user", "agent", "system_report"]
ReportKind = Literal["done", "blocked", "needs_you"]

# Context-window budgets (number of recent messages fed verbatim to the reasoner;
# older history is represented by the running summary). Local models get a smaller
# window because their context is shorter.
CLOUD_WINDOW_MESSAGES = 30
LOCAL_WINDOW_MESSAGES = 10


@dataclass(frozen=True)
class ChatMessage:
    id: str
    project_id: str
    role: Role
    text: str
    segment_id: str
    created_at: str
    requirement_id: str | None = None
    ticket_id: str | None = None
    report_kind: ReportKind | None = None
    attachment_ids: list[str] = field(default_factory=list)


class ChatAttachmentLike(Protocol):
    id: str
    filename: str
    mime: str
    size: int
    kind: str
    stored_path: str


@dataclass(frozen=True)
class WorkItem:
    """A discrete piece of work the reasoner extracted from the conversation."""

    title: str
    prompt: str


@dataclass(frozen=True)
class ReasonerTurn:
    """What the reasoner returns for one user message.

    ``reply`` is the conversational text shown to the user. ``work_items`` is the
    set of committed pieces of work to file as BACKLOG proposals — empty when the
    turn is discussion only (the key ambiguity guard: discussion != a ticket).
    ``updated_summary`` lets the reasoner roll the running summary forward.
    """

    reply: str
    work_items: list[WorkItem] = field(default_factory=list)
    updated_summary: str | None = None


@dataclass(frozen=True)
class ChatTurnResult:
    messages: list[ChatMessage]
    filed_requirement_ids: list[str] = field(default_factory=list)


class ChatRepository(Protocol):
    """Persistence for the per-project thread. Implemented by Codex (Lane 2)."""

    def append_message(
        self,
        *,
        project_id: str,
        role: Role,
        text: str,
        segment_id: str,
        requirement_id: str | None = None,
        ticket_id: str | None = None,
        report_kind: ReportKind | None = None,
        attachment_ids: list[str] | None = None,
    ) -> ChatMessage: ...

    def list_messages(
        self,
        project_id: str,
        *,
        segment_id: str | None = None,
        after: str | None = None,
        limit: int | None = None,
    ) -> list[ChatMessage]: ...

    def active_segment_id(self, project_id: str) -> str: ...

    def get_summary(self, project_id: str, segment_id: str) -> str: ...

    def set_summary(self, project_id: str, segment_id: str, summary: str) -> None: ...

    def attachments_by_ids(
        self,
        project_id: str,
        attachment_ids: list[str],
    ) -> list[ChatAttachmentLike]: ...


class ChatReasoner(Protocol):
    """Cloud or local LLM that drives the conversation. ``is_local`` selects the
    context-window budget."""

    is_local: bool

    def respond(
        self,
        *,
        summary: str,
        recent: list[ChatMessage],
        user_text: str,
    ) -> ReasonerTurn: ...


class RequirementGateway(Protocol):
    """Files a described requirement as BACKLOG proposals and returns its id.

    Wraps ``RequirementService.decompose_preview`` so chat_flow stays decoupled
    from the requirement model and context injector.
    """

    def file_backlog_proposal(
        self,
        *,
        project_id: str,
        title: str,
        prompt: str,
        attachment_ids: list[str] | None = None,
    ) -> str: ...


def window_size(reasoner: ChatReasoner) -> int:
    return LOCAL_WINDOW_MESSAGES if getattr(reasoner, "is_local", False) else CLOUD_WINDOW_MESSAGES


def _restate(work_items: list[WorkItem]) -> str:
    """Explicit intent restatement before anything is filed — the ambiguity guard."""
    n = len(work_items)
    titles = "; ".join(item.title for item in work_items)
    noun = "item" if n == 1 else "items"
    return (
        f"I heard {n} {noun} of work — filing as {'a proposal' if n == 1 else 'proposals'} "
        f"in Backlog for your approval: {titles}."
    )


class ChatService:
    """Stateless orchestration over injected Protocols; all state lives in the repo."""

    def __init__(
        self,
        repository: ChatRepository,
        reasoner: ChatReasoner,
        requirements: RequirementGateway,
    ) -> None:
        self.repository = repository
        self.reasoner = reasoner
        self.requirements = requirements

    def handle_user_message(
        self,
        project_id: str,
        user_text: str,
        attachment_ids: list[str] | None = None,
    ) -> ChatTurnResult:
        text = user_text.strip()
        if not text:
            raise ValueError("Message text cannot be empty")
        cleaned_attachment_ids = [
            item.strip() for item in (attachment_ids or []) if isinstance(item, str) and item.strip()
        ]

        segment_id = self.repository.active_segment_id(project_id)
        produced: list[ChatMessage] = []

        produced.append(
            self.repository.append_message(
                project_id=project_id,
                role="user",
                text=text,
                segment_id=segment_id,
                attachment_ids=cleaned_attachment_ids,
            )
        )

        summary = self.repository.get_summary(project_id, segment_id)
        recent = self.repository.list_messages(
            project_id, segment_id=segment_id, limit=window_size(self.reasoner)
        )
        attachment_context = _attachment_context_for_reasoner(
            self.repository.attachments_by_ids(project_id, cleaned_attachment_ids)
        )
        reasoner_user_text = _user_text_with_attachment_context(text, attachment_context)
        turn = self.reasoner.respond(summary=summary, recent=recent, user_text=reasoner_user_text)

        filed_ids: list[str] = []
        if turn.work_items:
            restatement = _restate(turn.work_items)
            reply_text = f"{restatement}\n\n{turn.reply}".strip() if turn.reply else restatement
            for item in turn.work_items:
                requirement_id = self.requirements.file_backlog_proposal(
                    project_id=project_id,
                    title=item.title,
                    prompt=item.prompt,
                    attachment_ids=cleaned_attachment_ids,
                )
                filed_ids.append(requirement_id)
            produced.append(
                self.repository.append_message(
                    project_id=project_id,
                    role="agent",
                    text=reply_text,
                    segment_id=segment_id,
                    requirement_id=filed_ids[0] if filed_ids else None,
                )
            )
        elif turn.reply:
            produced.append(
                self.repository.append_message(
                    project_id=project_id, role="agent", text=turn.reply, segment_id=segment_id
                )
            )

        if turn.updated_summary is not None:
            self.repository.set_summary(project_id, segment_id, turn.updated_summary)

        return ChatTurnResult(messages=produced, filed_requirement_ids=filed_ids)


def _user_text_with_attachment_context(user_text: str, attachment_context: str) -> str:
    if not attachment_context:
        return user_text
    return f"{user_text}\n\nAttached files available to the agent:\n{attachment_context}"


def _attachment_context_for_reasoner(attachments: list[ChatAttachmentLike]) -> str:
    if not attachments:
        return ""
    parts: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        payload = build_attachment_context(
            {
                "id": attachment.id,
                "type": attachment.kind,
                "value": attachment.stored_path,
                "filename": attachment.filename,
                "mime": attachment.mime,
                "size": attachment.size,
            }
        )
        header = (
            f"[Attachment {index}: {attachment.filename} | "
            f"{attachment.kind} | {attachment.mime} | {attachment.size} bytes]"
        )
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            suffix = "\n[Attachment content truncated]" if payload.get("content_truncated") else ""
            parts.append(f"{header}\n{content.strip()}{suffix}")
        elif attachment.kind == "image":
            parts.append(f"{header}\nImage content is attached, but no text was extracted.")
        else:
            parts.append(f"{header}\nFile content could not be extracted as text.")
    return "\n\n".join(parts)
