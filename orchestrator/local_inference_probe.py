"""Diagnostics for local-model prompt / context size (R-102)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.context.injector import estimate_tokens
from orchestrator.models.ticket import Ticket


def local_inference_probe_path() -> str | None:
    return os.environ.get("HAAO_R102_CONTEXT_PROBE")


def log_local_inference_context(
    phase: str,
    *,
    ticket: Ticket,
    target_file: str,
    prompt: str,
    response: str = "",
) -> None:
    output_path = local_inference_probe_path()
    if not output_path:
        return

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    context_lines = [
        f"  - {file.path}: {len(file.content)} chars, ~{estimate_tokens(file.content)} tokens"
        + (" (truncated)" if file.truncated else "")
        for file in ticket.context.files
    ]
    block = "\n".join(
        [
            f"=== {datetime.now(UTC).isoformat()} phase={phase} ===",
            f"ticket_id={ticket.id} target_file={target_file}",
            f"prompt_chars={len(prompt)} prompt_tokens_est={estimate_tokens(prompt)}",
            f"context_files ({len(ticket.context.files)}):",
            *(context_lines or ["  (none)"]),
        ]
    )
    if phase == "after":
        block += (
            f"\nresponse_chars={len(response)} "
            f"response_tokens_est={estimate_tokens(response)}"
        )
    path.write_text(
        (path.read_text(encoding="utf-8") + block + "\n\n") if path.is_file() else block + "\n\n",
        encoding="utf-8",
    )


def should_stop_after_context_probe() -> bool:
    return os.environ.get("HAAO_R102_CONTEXT_PROBE_STOP") == "1"


class LocalInferenceContextProbeStop(RuntimeError):
    """Raised to skip LM Studio after context metrics were captured."""
