#!/usr/bin/env python3
"""Live smoke test for the Claude Tech Lead client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clients.claude_po import ClaudePOError, ClaudeTechLeadClient
from orchestrator.config import get_settings
from orchestrator.models.ticket import Ticket


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_TICKET = PROJECT_ROOT / "atomic_ticket.example.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Claude Tech Lead API")
    parser.add_argument(
        "--mode",
        choices=("decompose", "audit"),
        default="decompose",
        help="Which Tech Lead operation to exercise",
    )
    parser.add_argument(
        "--requirement",
        default="Add a one-line comment to README.md explaining HAAO.",
        help="Requirement text for decompose mode",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.claude_api_key:
        print("FAIL: CLAUDE_API_KEY is not configured", file=sys.stderr)
        return 1

    print(f"Mode: {args.mode}")
    try:
        with ClaudeTechLeadClient(
            settings.claude_api_key,
            model=settings.claude_model,
            timeout_sec=120.0,
        ) as client:
            if args.mode == "decompose":
                tickets = client.decompose(
                    args.requirement,
                    "Repository summary:\n- Root: HAAO\n- README.md exists",
                    scope_paths=["README.md"],
                )
                print(f"Received {len(tickets)} ticket(s)")
                print(json.dumps(tickets[:1], indent=2, ensure_ascii=False))
            else:
                ticket = Ticket.from_dict(json.loads(EXAMPLE_TICKET.read_text(encoding="utf-8")))
                result = client.audit(ticket, ticket.result.diff if ticket.result else "")
                print(json.dumps({"verdict": result.verdict, "feedback": result.feedback}, indent=2))
    except ClaudePOError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
