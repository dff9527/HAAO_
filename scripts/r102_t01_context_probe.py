#!/usr/bin/env python3
"""Capture T-01 local inference prompt/context metrics without LM Studio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.benchmark_runner import (  # noqa: E402
    ensure_benchmark_repo,
    git,
    install_probe,
    load_manifest,
    reset_repo_to_baseline,
)
from orchestrator.context.injector import ContextInjector, estimate_tokens  # noqa: E402
from orchestrator.execution_loop import (  # noqa: E402
    _refresh_target_context,
    build_file_rewrite_prompt,
)
from orchestrator.local_inference_probe import log_local_inference_context  # noqa: E402
from orchestrator.models.ticket import Ticket  # noqa: E402

OUTPUT = PROJECT_ROOT / "output" / "r102_t01_context_probe.txt"
TASK_ID = "T-01"


def main() -> int:
    manifest = load_manifest(PROJECT_ROOT / "benchmarks" / "r102_manifest.json")
    task = next(item for item in manifest.tasks if item.id == TASK_ID)
    repo_def = manifest.repos[task.repo]
    repo_path = ensure_benchmark_repo(repo_def)
    reset_repo_to_baseline(repo_path, repo_def.ref)
    install_probe(repo_path, task.id)
    git(repo_path, "add", "-A")
    committed = git(repo_path, "commit", "-q", "-m", "harness: r102 probe (throwaway)")
    if committed.returncode != 0:
        print(f"probe commit failed: {committed.stderr.strip()}", file=sys.stderr)
        return 1

    if OUTPUT.is_file():
        OUTPUT.unlink()
    os.environ["HAAO_R102_CONTEXT_PROBE"] = str(OUTPUT)

    ticket_dict = {
        "id": "T-001",
        "title": "Harden CSV formula injection export",
        "type": "bugfix",
        "status": "ready",
        "task": {
            "description": task.requirement,
            "target_files": [
                "src/tablib/formats/_csv.py",
                "tests/test_tablib.py",
            ],
            "constraints": [
                "Modify only src/tablib/formats/_csv.py and tests/test_tablib.py",
                "Do not break existing tests/test_tablib.py",
            ],
        },
        "context": {"files": []},
        "definition_of_done": {
            "tests": [
                {
                    "command": task.dod,
                    "expect": "pass",
                    "timeout_sec": 120,
                }
            ]
        },
        "execution": {
            "assigned_model": "qwen3-coder-next",
            "retry_budget": 2,
            "attempts": 0,
            "escalate_to": "tech_lead",
        },
        "audit": {"verdict": "pending"},
    }
    injector = ContextInjector(repo_path)
    ticket = injector.inject(Ticket.from_dict(ticket_dict))
    ticket = _refresh_target_context(ticket, repo_path)

    summary: dict = {
        "task_id": TASK_ID,
        "repo": str(repo_path),
        "target_files": ticket.task.target_files,
        "context_token_estimate": ticket.context.token_estimate,
        "per_target_prompts": [],
    }

    for target_file in ticket.task.target_files:
        prompt = build_file_rewrite_prompt(ticket, target_file)
        log_local_inference_context(
            "before",
            ticket=ticket,
            target_file=target_file,
            prompt=prompt,
        )
        summary["per_target_prompts"].append(
            {
                "target_file": target_file,
                "prompt_chars": len(prompt),
                "prompt_tokens_est": estimate_tokens(prompt),
                "context_files": [
                    {
                        "path": file.path,
                        "chars": len(file.content),
                        "tokens_est": estimate_tokens(file.content),
                        "truncated": file.truncated,
                        "reason": file.reason,
                    }
                    for file in ticket.context.files
                ],
            }
        )

    OUTPUT.write_text(
        OUTPUT.read_text(encoding="utf-8")
        + "\n--- JSON summary ---\n"
        + json.dumps(summary, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")
    for row in summary["per_target_prompts"]:
        print(
            f"{row['target_file']}: prompt ~{row['prompt_tokens_est']} tokens "
            f"({row['prompt_chars']} chars), context files={len(row['context_files'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
