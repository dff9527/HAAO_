from __future__ import annotations

from pathlib import Path

from orchestrator.context.retrieval import retrieve_related_context
from orchestrator.context.untrusted import wrap_untrusted_context
from orchestrator.models.ticket import Context, ContextFile, Ticket
from orchestrator.redaction import redact_text

DEFAULT_MAX_TOKENS = 8000
CHARS_PER_TOKEN_ESTIMATE = 4


class ContextInjector:
    """Read target files from a repo and populate ticket.context."""

    def __init__(self, repo_root: str | Path, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_tokens = max_tokens

    def inject(self, ticket: Ticket) -> Ticket:
        context_files: list[ContextFile] = []
        remaining_tokens = self.max_tokens
        used_tokens = 0

        for relative_path in ticket.task.target_files:
            file_path = self._resolve_repo_path(relative_path)
            content = file_path.read_text(encoding="utf-8")
            truncated = False
            reason = "File to modify"

            file_tokens = estimate_tokens(content)
            if file_tokens > remaining_tokens:
                content = truncate_to_token_budget(content, remaining_tokens)
                truncated = True
                reason = "Truncated to fit context token budget"
                file_tokens = estimate_tokens(content)

            context_files.append(
                ContextFile(
                    path=relative_path,
                    content=wrap_untrusted_context(label=relative_path, content=redact_text(content)),
                    truncated=truncated,
                    reason=reason,
                )
            )
            remaining_tokens = max(0, remaining_tokens - file_tokens)
            used_tokens += file_tokens

        related_context = retrieve_related_context(self.repo_root, ticket.task.target_files)
        for related in related_context:
            if remaining_tokens <= 0:
                break
            content = related.content
            truncated = False
            reason = related.reason
            file_tokens = estimate_tokens(content)
            if file_tokens > remaining_tokens:
                content = truncate_to_token_budget(content, remaining_tokens)
                truncated = True
                reason = "Related context truncated to fit token budget"
                file_tokens = estimate_tokens(content)

            context_files.append(
                ContextFile(
                    path=related.path,
                    content=wrap_untrusted_context(label=related.path, content=redact_text(content)),
                    truncated=truncated,
                    reason=reason,
                )
            )
            remaining_tokens = max(0, remaining_tokens - file_tokens)
            used_tokens += file_tokens

        token_estimate = used_tokens
        related_symbols = list(ticket.context.related_symbols)
        for related in related_context:
            for symbol in related.symbols:
                label = f"{related.path}:{symbol}"
                if label not in related_symbols:
                    related_symbols.append(label)
        updated_context = Context(
            files=context_files,
            related_symbols=related_symbols,
            notes=ticket.context.notes,
            token_estimate=token_estimate,
        )
        return ticket.model_copy(update={"context": updated_context})

    def _resolve_repo_path(self, relative_path: str) -> Path:
        candidate = (self.repo_root / relative_path).resolve()
        if not candidate.is_relative_to(self.repo_root):
            raise ValueError(f"Refusing to read path outside repo root: {relative_path}")
        if not candidate.is_file():
            raise FileNotFoundError(f"Target file not found: {relative_path}")
        return candidate


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def truncate_to_token_budget(text: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    max_chars = token_budget * CHARS_PER_TOKEN_ESTIMATE
    if len(text) <= max_chars:
        return text
    return text[:max_chars]
