#!/usr/bin/env python3
"""R-102: benchmark HAAO on real open-source repositories.

Runs decompose → execute → diff gate → cloud audit for each task in
benchmarks/r102_manifest.json, collects JSON metrics, and resets repos afterward.

Example:
  PYTHONPATH=. python scripts/benchmark_real_repos.py --setup-repos
  PYTHONPATH=. python scripts/benchmark_real_repos.py --trials 2 --out output/r102_full.json
  PYTHONPATH=. python scripts/benchmark_real_repos.py --task-ids C-01 --trials 1
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.benchmark_runner import (
    DEFAULT_MAX_TARGET_FILE_BYTES,
    aggregate_trial_results,
    ensure_benchmark_repo,
    load_manifest,
    repo_checkout_summary,
    run_baseline_probe_check,
    run_requirement_trial,
)
from orchestrator.config import get_settings
from orchestrator.execution_loop import (
    DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
    DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmarks" / "r102_manifest.json"


def _trial_to_dict(trial) -> dict:
    payload = asdict(trial)
    payload["tickets"] = [asdict(snapshot) for snapshot in trial.tickets]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="R-102 real-repo benchmark harness")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to r102 manifest JSON",
    )
    parser.add_argument("--setup-repos", action="store_true", help="Clone/reset benchmark repos only")
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Run harness probe + existing_tests on clean baseline only (no model)",
    )
    parser.add_argument("--trials", type=int, default=2, help="Trials per task (default: 2)")
    parser.add_argument("--task-ids", default="", help="Comma-separated task ids to run (default: all)")
    parser.add_argument("--local-model", default="qwen3-coder-next")
    parser.add_argument(
        "--local-timeout",
        type=float,
        default=900.0,
        help="LM Studio request timeout in seconds (default: 900)",
    )
    parser.add_argument(
        "--max-target-file-bytes",
        type=int,
        default=DEFAULT_MAX_TARGET_FILE_BYTES,
        help=(
            "Exclude a trial as infra_error when a decomposed target exceeds "
            f"this size (default: {DEFAULT_MAX_TARGET_FILE_BYTES})"
        ),
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help=(
            "Maximum tokens in one local-model response. Files whose estimated whole-file "
            "output exceeds this cap also use SEARCH/REPLACE patch mode. Defaults to "
            "LOCAL_MAX_OUTPUT_TOKENS from Settings "
            f"({DEFAULT_LOCAL_MAX_OUTPUT_TOKENS} when unset)."
        ),
    )
    parser.add_argument(
        "--patch-mode-threshold-tokens",
        type=int,
        default=None,
        help=(
            "Use SEARCH/REPLACE patch mode when the estimated whole-file output exceeds "
            "this threshold. Defaults to LOCAL_PATCH_MODE_THRESHOLD_TOKENS from Settings "
            f"({DEFAULT_PATCH_MODE_THRESHOLD_TOKENS} when unset)."
        ),
    )
    parser.add_argument("--out", default=None, help="Write JSON report to this path")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N selected tasks")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    selected_ids = {item.strip() for item in args.task_ids.split(",") if item.strip()}
    tasks = [
        task
        for task in manifest.tasks
        if not selected_ids or task.id in selected_ids
    ]
    if args.limit > 0:
        tasks = tasks[: args.limit]

    if args.setup_repos:
        for repo_def in manifest.repos.values():
            path = ensure_benchmark_repo(repo_def)
            summary = repo_checkout_summary(repo_def)
            print(
                f"ready: {repo_def.name} @ {repo_def.ref} -> {path} "
                f"(HEAD={summary['head_sha'][:12]} match={summary['matches_pin']})"
            )
        return 0

    if args.baseline_only:
        if not tasks:
            print("No tasks selected.", file=sys.stderr)
            return 1
        print(f"Baseline-only probe check: {len(tasks)} task(s)")
        print("-" * 60)
        rows = []
        failed = 0
        for task in tasks:
            repo_def = manifest.repos[task.repo]
            row = run_baseline_probe_check(task=task, repo_def=repo_def)
            rows.append(row)
            ok = row.baseline_failed_first and row.existing_tests_still_green and not row.error
            if not ok:
                failed += 1
            print(
                f"[{task.id}] baseline_failed_first={row.baseline_failed_first} "
                f"existing_green={row.existing_tests_still_green} "
                f"{'OK' if ok else 'FAIL'}",
                flush=True,
            )
            if row.error:
                print(f"  error: {row.error[:200]}", flush=True)
        print("-" * 60)
        print(f"passed {len(tasks) - failed}/{len(tasks)}")
        out_path = args.out or str(PROJECT_ROOT / "output" / "r102_baseline_probe_check.json")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps(
                {
                    "benchmark": "R-102-baseline-only",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "passed": len(tasks) - failed,
                    "total": len(tasks),
                    "checks": [asdict(row) for row in rows],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Report written to {out_path}")
        return 1 if failed else 0

    if not tasks:
        print("No tasks selected.", file=sys.stderr)
        return 1

    settings = get_settings()
    if not settings.claude_api_key:
        print("CLAUDE_API_KEY is not configured", file=sys.stderr)
        return 1
    max_output_tokens = (
        args.max_output_tokens
        if args.max_output_tokens is not None
        else settings.local_max_output_tokens
    )
    if max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    patch_mode_threshold_tokens = (
        args.patch_mode_threshold_tokens
        if args.patch_mode_threshold_tokens is not None
        else settings.local_patch_mode_threshold_tokens
    )
    if patch_mode_threshold_tokens < 1:
        parser.error("--patch-mode-threshold-tokens must be positive")

    print(f"Manifest : {args.manifest}")
    print(f"Tasks    : {len(tasks)} x {max(1, args.trials)} trials")
    print(f"Cloud    : {settings.claude_model}")
    print(f"Local    : {args.local_model} (timeout {args.local_timeout:.0f}s)")
    print(f"Output cap: {max_output_tokens} tokens per target file")
    print(f"Patch threshold: {patch_mode_threshold_tokens} estimated whole-file tokens")
    print("-" * 60)

    trials = []
    for task in tasks:
        repo_def = manifest.repos[task.repo]
        for trial_number in range(1, max(1, args.trials) + 1):
            print(f"[{task.id}] trial {trial_number}/{args.trials} ({task.category}) starting", flush=True)
            trial = run_requirement_trial(
                task=task,
                repo_def=repo_def,
                settings=settings,
                trial=trial_number,
                local_model=args.local_model,
                local_timeout_sec=args.local_timeout,
                max_target_file_bytes=args.max_target_file_bytes,
                max_output_tokens=max_output_tokens,
                patch_mode_threshold_tokens=patch_mode_threshold_tokens,
            )
            trials.append(trial)
            print(
                f"[{task.id}] trial {trial_number}/{args.trials} "
                f"{trial.result} tickets={trial.ticket_count} "
                f"attempts={trial.max_attempts} "
                f"baseline_failed_first={trial.baseline_failed_first} "
                f"existing_green={trial.existing_tests_still_green} "
                f"verified_one_shot={trial.verified_one_shot} "
                f"cloud=${trial.cloud_cost_usd:.4f} "
                f"local={trial.local_inference_sec:.1f}s "
                f"total={trial.total_duration_sec:.1f}s",
                flush=True,
            )
            if trial.error:
                print(f"  error: {trial.error[:200]}", flush=True)

    summary = aggregate_trial_results(trials)
    by_category: dict[str, list] = {}
    for trial in trials:
        by_category.setdefault(trial.category, []).append(trial)
    category_summary = {
        category: aggregate_trial_results(items)
        for category, items in by_category.items()
    }

    print("-" * 60)
    print(f"trials total           = {summary['trials_total']}")
    print(f"trials counted         = {summary['trials_counted']}")
    print(f"excluded (baseline ok) = {summary['trials_excluded_baseline_passed']}")
    if summary["trials_counted"]:
        print(
            f"baseline failed first  = {summary['baseline_failed_first']}/{summary['trials_total']} "
            f"({summary['baseline_failed_first_rate']:.0%})"
        )
        print(
            f"verified one-shot      = {summary['one_shot']}/{summary['trials_counted']} "
            f"({summary['one_shot_rate']:.0%})"
        )
        print(
            f"verified local finish  = {summary['local_finish']}/{summary['trials_counted']} "
            f"({summary['local_finish_rate']:.0%})"
        )
        print(
            f"escalation rate        = {summary['escalated']}/{summary['trials_counted']} "
            f"({summary['escalation_rate']:.0%})"
        )
        print(
            f"mechanical failure rate= {summary['mechanical_failures']}/{summary['trials_counted']} "
            f"({summary['mechanical_failure_rate']:.0%})"
        )
        print(
            f"existing tests green   = {summary['existing_tests_still_green']}/{summary['trials_counted']} "
            f"({summary['existing_tests_still_green_rate']:.0%})"
        )
        print(f"median local inference = {summary['median_local_inference_sec']:.2f}s")
        print(f"total cloud cost       = ${summary['total_cloud_cost_usd']:.4f}")
    if summary["errors"]:
        print(f"harness errors         = {summary['errors']}/{summary['trials_total']}")
    if summary["infra_errors"]:
        print(f"infra errors           = {summary['infra_errors']}/{summary['trials_total']}")
    if summary["size_excluded"]:
        print(f"size excluded          = {summary['size_excluded']}/{summary['trials_total']}")

    report = {
        "benchmark": "R-102",
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(Path(args.manifest).resolve()),
        "local_model": args.local_model,
        "local_timeout_sec": args.local_timeout,
        "max_target_file_bytes": args.max_target_file_bytes,
        "max_output_tokens": max_output_tokens,
        "patch_mode_threshold_tokens": patch_mode_threshold_tokens,
        "cloud_model": settings.claude_model,
        "summary": summary,
        "by_category": category_summary,
        "trials": [_trial_to_dict(trial) for trial in trials],
    }

    out_path = args.out
    if out_path is None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        out_path = str(PROJECT_ROOT / "output" / f"r102_{stamp}.json")
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
