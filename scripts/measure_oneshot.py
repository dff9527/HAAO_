#!/usr/bin/env python3
"""Measure the local coder model's one-shot success rate, end to end.

Chains the validation loop in one shot:
  1. Claude Tech Lead decomposes a requirement into atomic tickets.
  2. Context is injected from a TARGET git repo (existing files only).
  3. Each ticket is executed by the local model; tests decide pass/fail/retry.
  4. A report is printed: one-shot rate / local-finish rate / escalation rate.

This is the "change granularity -> re-test" tool. To tune granularity, just rerun
with a different --requirement or --scope and compare the numbers.

All services point at --target-repo, so the local model's edits NEVER touch the
HAAO repo itself.

WARNING (blast radius): on every retry, and between tickets, this runs
`git reset --hard` + `git clean -fd` on the TARGET repo. The target MUST be a git
repo whose baseline (stub files + failing tests) is already committed and clean.
Uncommitted work in the target repo WILL be destroyed.

Note: the orchestrator can only MODIFY existing files (context injection reads them).
A "create a new file" requirement will be skipped. Pre-create + commit stub files in
the target repo, then write requirements that fix/modify them.
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from clients.claude_po import ClaudeTechLeadClient
from clients.lmstudio import ChatMessage, LMStudioClient
from orchestrator.config import get_settings
from orchestrator.context.injector import ContextInjector
from orchestrator.db.sqlite import TicketRepository, connect
from orchestrator.execution_loop import ExecutionLoop, format_test_results
from orchestrator.models.ticket import Ticket
from orchestrator.runner.dod_runner import TestRunner
from orchestrator.state_machine import TicketStateService

# Local execution is finished once tests pass and the diff reaches the human
# review gate. Later states depend on human/cloud review and are not evidence of
# local coder failure.
LOCAL_FINISH_STATES = {"diff_pending", "review", "awaiting_acceptance", "done"}


def _classify_trial(status: str, attempts: int) -> str:
    if status not in LOCAL_FINISH_STATES:
        return "blocked"
    return "one_shot" if attempts == 0 else "retry_then_pass"


def _clean_path(raw: str) -> Path:
    # tolerate placeholder angle brackets that get copied by accident
    return Path(raw.strip().lstrip("<").rstrip(">")).expanduser().resolve()


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(target), *args],
        capture_output=True,
        text=True,
        shell=False,
    )


def _assert_clean_git_repo(target: Path, allow_dirty: bool) -> None:
    if not target.is_dir():
        sys.exit(f"Target repo does not exist: {target}")
    if not (target / ".git").exists():
        sys.exit(
            f"Target is not a git repo: {target}\n"
            f"  cd {target} && git init && git add -A && git commit -m baseline"
        )
    status = _git(target, "status", "--porcelain").stdout.strip()
    if status and not allow_dirty:
        sys.exit(
            f"Target repo has uncommitted changes; commit your baseline first "
            f"(retries run git reset --hard on it).\nDirty:\n{status}"
        )


def _reset_target(target: Path) -> None:
    _git(target, "reset", "--hard")
    _git(target, "clean", "-fd")


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def _whole_file_attempt(target: Path, ticket: Ticket, lmstudio: LMStudioClient, model: str):
    """EXPERIMENT: local model returns the FULL updated file content (no diff, no git apply).

    Returns (result, detail, empty) where result is 'one_shot' | 'blocked' | 'error'.
    """
    content_by_path = {f.path: f.content for f in ticket.context.files}
    tests = "\n".join(f"- {t.command}" for t in ticket.definition_of_done.tests)
    constraints = "\n".join(f"- {c}" for c in ticket.task.constraints) or "(none)"
    for path in ticket.task.target_files:
        current = content_by_path.get(path, "")
        prompt = (
            f"Return the COMPLETE, updated contents of the file `{path}` so the task is done "
            f"and the tests pass. Output ONLY the raw file content — no markdown fences, no "
            f"commentary.\n\nTask:\n{ticket.task.description}\n\nConstraints:\n{constraints}\n\n"
            f"Current contents of {path}:\n{current}\n\nTests that must pass:\n{tests}\n"
        )
        out = lmstudio.chat_completion(
            model=model,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.2,
        )
        body = _strip_code_fences(out)
        if not body.strip():
            return "blocked", f"empty file output for {path}", True
        dest = (target / path).resolve()
        if not dest.is_relative_to(target.resolve()):
            return "error", f"path outside repo: {path}", False
        dest.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    results = TestRunner(cwd=target).run_ticket_tests(ticket)
    passed = all(r.status == "pass" for r in results)
    return ("one_shot" if passed else "blocked"), format_test_results(results), False


def _build_repo_context(target: Path, scope: list[str]) -> str:
    files = _git(target, "ls-files").stdout.splitlines()
    if scope:
        files = [f for f in files if any(f.startswith(s.rstrip("/")) for s in scope)]
    listing = "\n".join(f"- {f}" for f in files[:200]) or "- (no tracked files)"
    return f"Repository: {target.name}\nTracked files in scope:\n{listing}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure local model one-shot rate")
    parser.add_argument("--target-repo", required=True, help="External git repo to operate on")
    parser.add_argument("--requirement", required=True, help="Product Owner requirement prompt")
    parser.add_argument("--scope", default="", help="Comma-separated relative scope paths")
    parser.add_argument("--local-model", default="qwen3-coder-next")
    parser.add_argument("--retry-budget", type=int, default=None, help="Override per-ticket retry budget")
    parser.add_argument("--out", default=None, help="Write a JSON report to this path")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each ticket N times to estimate the one-shot probability")
    parser.add_argument("--whole-file", action="store_true",
                        help="Legacy compatibility flag; the default execution loop now uses whole-file output")
    parser.add_argument("--allow-dirty", action="store_true", help="Skip the clean-tree check (unsafe)")
    args = parser.parse_args()

    target = _clean_path(args.target_repo)
    scope = [s.strip() for s in args.scope.split(",") if s.strip()]
    _assert_clean_git_repo(target, args.allow_dirty)

    settings = get_settings()
    if not settings.claude_api_key:
        sys.exit("CLAUDE_API_KEY is not configured")

    print(f"Target repo : {target}")
    print(f"Cloud model : {settings.claude_model}")
    print(f"Local model : {args.local_model}")
    print(f"Scope       : {scope or '(whole repo)'}")
    print("-" * 60)

    # 1. decompose
    tech_lead = ClaudeTechLeadClient(settings.claude_api_key, model=settings.claude_model)
    try:
        ticket_dicts = tech_lead.decompose(
            args.requirement,
            _build_repo_context(target, scope),
            scope_paths=scope or None,
        )
    finally:
        tech_lead.close()
    print(f"Decomposed into {len(ticket_dicts)} ticket(s)")

    # 2. inject context + persist into a throwaway DB
    db_path = Path(tempfile.mkstemp(suffix=".sqlite3", prefix="haao-measure-")[1])
    connection = connect(db_path)
    repository = TicketRepository(connection)
    state_service = TicketStateService(repository)
    injector = ContextInjector(target)
    lmstudio = LMStudioClient(settings.lmstudio_base_url, timeout_sec=180.0)

    templates: list[dict] = []
    skipped_rows: list[dict] = []
    for td in ticket_dicts:
        if args.retry_budget is not None:
            td.setdefault("execution", {})["retry_budget"] = args.retry_budget
        td["execution"]["assigned_model"] = args.local_model
        ticket = Ticket.from_dict(td)
        try:
            ticket = injector.inject(ticket)
        except FileNotFoundError as exc:
            skipped_rows.append({"id": ticket.id, "title": ticket.title, "result": "skipped",
                                 "reason": f"target file missing (modify-only MVP): {exc}"})
            continue
        templates.append(ticket.to_dict())

    # 3. run each ticket `repeat` times from a clean baseline.
    # One-shot success is a PROBABILITY (the local model runs at temperature>0), so a
    # single trial is a coin flip; repeating and averaging is the only honest measure.
    repeat = max(1, args.repeat)
    loop = ExecutionLoop(
        repository,
        state_service,
        lmstudio,
        repo_root=target,
        max_output_tokens=settings.local_max_output_tokens,
        patch_mode_threshold_tokens=settings.local_patch_mode_threshold_tokens,
    )
    per_ticket: list[dict] = []
    trials: list[dict] = []
    for tmpl in templates:
        tid, title = tmpl["id"], tmpl.get("title", "")
        outcomes: list[str] = []
        for k in range(repeat):
            print(f"[{tid}] trial {k + 1}/{repeat} starting", flush=True)
            started_at = time.monotonic()
            _reset_target(target)
            try:
                if args.whole_file:
                    res, detail, empty = _whole_file_attempt(
                        target, Ticket.from_dict(copy.deepcopy(tmpl)), lmstudio, args.local_model)
                    attempts = 0
                else:
                    fresh = copy.deepcopy(tmpl)
                    fresh["status"] = "backlog"
                    fresh.setdefault("execution", {})["attempts"] = 0
                    fresh.pop("result", None)
                    fresh_ticket = Ticket.from_dict(fresh)
                    if repository.get(tid) is None:
                        repository.create(fresh_ticket)   # first trial: insert
                    else:
                        repository.save(fresh_ticket)     # later trials: reset to baseline
                    loop.run_ticket(tid)
                    t = repository.get(tid)
                    attempts = t.execution.attempts
                    res = _classify_trial(str(t.status), attempts)
                    detail = (t.result.test_output if t.result else "") or ""
                    empty = not ((t.result.diff if t.result else "") or "").strip()
            except Exception as exc:  # noqa: BLE001 - record any failure, keep measuring
                res, attempts, detail, empty = "error", None, str(exc), False
            duration_sec = round(time.monotonic() - started_at, 2)
            outcomes.append(res)
            trials.append({"id": tid, "trial": k + 1, "result": res,
                           "attempts": attempts, "detail": detail, "empty_diff": empty,
                           "duration_sec": duration_sec})
            print(
                f"[{tid}] trial {k + 1}/{repeat} {res} "
                f"attempts={attempts} duration={duration_sec:.2f}s",
                flush=True,
            )
        per_ticket.append({
            "id": tid, "title": title, "trials": repeat,
            "one_shot": outcomes.count("one_shot"),
            "finished": outcomes.count("one_shot") + outcomes.count("retry_then_pass"),
            "blocked": outcomes.count("blocked"),
            "error": outcomes.count("error"),
        })

    lmstudio.close()
    connection.close()
    _reset_target(target)  # leave the target clean

    # 4. report (rates are over total trials)
    total = sum(p["trials"] for p in per_ticket)
    one = sum(p["one_shot"] for p in per_ticket)
    fin = sum(p["finished"] for p in per_ticket)
    blk = sum(p["blocked"] for p in per_ticket)
    err = sum(p["error"] for p in per_ticket)
    median_duration_sec = (
        round(statistics.median(t["duration_sec"] for t in trials), 2)
        if trials
        else 0.0
    )

    print("-" * 60)
    for p in per_ticket:
        print(f"  {p['id']:<8} one-shot {p['one_shot']}/{p['trials']}  "
              f"finished {p['finished']}/{p['trials']}  {p['title'][:40]}")
    for s in skipped_rows:
        print(f"  {s['id']:<8} skipped  {s.get('reason', '')[:50]}")
    print("-" * 60)
    if total:
        print(f"one-shot rate    = {one}/{total} = {one / total:.0%}   <-- main metric ({repeat}x/ticket)")
        print(f"local finish     = {fin}/{total} = {fin / total:.0%}")
        print(f"escalation rate  = {blk}/{total} = {blk / total:.0%}")
        print(f"median duration  = {median_duration_sec:.2f}s per trial")
        if err:
            print(f"harness errors   = {err}/{total}")
    else:
        print("No runnable tickets. Use modify-not-create requirements against existing files.")

    # Diagnostics: a few failing trials, to tell mechanical vs capability apart
    fails = [t for t in trials if t["result"] in {"blocked", "error"}][:3]
    if fails:
        print("\n=== sample failure detail (mechanical vs capability) ===")
        for t in fails:
            tail = ((t.get("detail") or "").strip())[-800:] or "(no output)"
            flag = " [EMPTY DIFF]" if t.get("empty_diff") else ""
            print(f"\n--- {t['id']} trial {t['trial']} {t['result']}{flag} ---\n{tail}")
        print("\n'git apply'/'patch'/'corrupt'/EMPTY DIFF = mechanical (try whole-file output). "
              "Real assertion failures = capability/granularity.")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": {"trials": total, "one_shot": one, "local_finish": fin,
                                    "escalated": blk, "errors": err, "repeat": repeat,
                                    "median_duration_sec": median_duration_sec},
                        "per_ticket": per_ticket, "trials": trials, "skipped": skipped_rows},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
