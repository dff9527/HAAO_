#!/usr/bin/env python3
"""Wave 4 proof report: run one R-102 task per real repo and emit JSON + Markdown.

This script reuses the R-102 runner and does not modify benchmark assets. By default it
selects the first task for each repo in benchmarks/r102_manifest.json, which currently
covers click, tablib, and marshmallow.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.benchmark_runner import (
    DEFAULT_MAX_TARGET_FILE_BYTES,
    aggregate_trial_results,
    load_manifest,
    run_requirement_trial,
)
from orchestrator.config import get_settings
from orchestrator.execution_loop import (
    DEFAULT_LOCAL_MAX_OUTPUT_TOKENS,
    DEFAULT_PATCH_MODE_THRESHOLD_TOKENS,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "benchmarks" / "r102_manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wave 4 real-repo benchmark proof report")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--task-ids", default="", help="Comma-separated task ids. Defaults to one task per repo.")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--local-model", default="qwen3-coder-next")
    parser.add_argument("--local-timeout", type=float, default=900.0)
    parser.add_argument("--max-target-file-bytes", type=int, default=DEFAULT_MAX_TARGET_FILE_BYTES)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--patch-mode-threshold-tokens", type=int, default=None)
    parser.add_argument("--out-json", default=None)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    tasks = _select_tasks(manifest, args.task_ids)
    if not tasks:
        parser.error("No benchmark tasks selected")

    settings = get_settings()
    if not settings.claude_api_key:
        parser.error("CLAUDE_API_KEY is not configured")
    max_output_tokens = args.max_output_tokens or settings.local_max_output_tokens
    patch_mode_threshold_tokens = args.patch_mode_threshold_tokens or settings.local_patch_mode_threshold_tokens

    trials = []
    for task in tasks:
        repo_def = manifest.repos[task.repo]
        for trial_number in range(1, max(1, args.trials) + 1):
            print(f"[{task.repo}:{task.id}] trial {trial_number}/{args.trials} starting", flush=True)
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
                f"[{task.repo}:{task.id}] {trial.result} "
                f"one_shot={trial.verified_one_shot} "
                f"local_finish={trial.verified_local_finish} "
                f"cloud=${trial.cloud_cost_usd:.4f} "
                f"total={trial.total_duration_sec:.1f}s",
                flush=True,
            )

    summary = aggregate_trial_results(trials)
    by_repo = {
        repo: aggregate_trial_results([trial for trial in trials if trial.repo == repo])
        for repo in sorted({trial.repo for trial in trials})
    }
    report = {
        "benchmark": "Wave-4-real-repo-proof",
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(Path(args.manifest).resolve()),
        "repos": sorted({task.repo for task in tasks}),
        "task_ids": [task.id for task in tasks],
        "local_model": args.local_model,
        "cloud_model": settings.claude_model,
        "trials_per_task": max(1, args.trials),
        "summary": summary,
        "by_repo": by_repo,
        "trials": [_trial_to_dict(trial) for trial in trials],
    }

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out_json = Path(args.out_json or PROJECT_ROOT / "output" / f"wave4_real_repo_benchmark_{stamp}.json")
    out_md = Path(args.out_md or PROJECT_ROOT / "output" / f"wave4_real_repo_benchmark_{stamp}.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(_markdown_report(report), encoding="utf-8")
    print(f"JSON report written to {out_json}")
    print(f"Markdown report written to {out_md}")
    return 0


def _select_tasks(manifest, task_ids: str):  # type: ignore[no-untyped-def]
    selected_ids = {item.strip() for item in task_ids.split(",") if item.strip()}
    if selected_ids:
        return [task for task in manifest.tasks if task.id in selected_ids]
    seen_repos: set[str] = set()
    selected = []
    for task in manifest.tasks:
        if task.repo in seen_repos:
            continue
        seen_repos.add(task.repo)
        selected.append(task)
        if len(seen_repos) >= 3:
            break
    return selected


def _trial_to_dict(trial) -> dict:  # type: ignore[no-untyped-def]
    payload = asdict(trial)
    payload["tickets"] = [asdict(snapshot) for snapshot in trial.tickets]
    return payload


def _markdown_report(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Wave 4 Real-Repo Benchmark Proof",
        "",
        f"Generated: {report['generated_at']}",
        f"Repos: {', '.join(report['repos'])}",
        f"Tasks: {', '.join(report['task_ids'])}",
        f"Local model: `{report['local_model']}`",
        f"Cloud model: `{report['cloud_model']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Trials counted | {summary['trials_counted']} / {summary['trials_total']} |",
        f"| Verified one-shot | {summary['one_shot']} ({summary['one_shot_rate']:.0%}) |",
        f"| Verified local finish | {summary['local_finish']} ({summary['local_finish_rate']:.0%}) |",
        f"| Escalated / blocked | {summary['escalated']} ({summary['escalation_rate']:.0%}) |",
        f"| Existing tests green | {summary['existing_tests_still_green']} ({summary['existing_tests_still_green_rate']:.0%}) |",
        f"| Median local inference | {summary['median_local_inference_sec']:.2f}s |",
        f"| Total cloud cost | ${summary['total_cloud_cost_usd']:.4f} |",
        "",
        "## By Repo",
        "",
        "| Repo | Counted | One-shot | Local finish | Escalation | Cloud cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for repo, row in report["by_repo"].items():
        lines.append(
            f"| {repo} | {row['trials_counted']} | {row['one_shot_rate']:.0%} | "
            f"{row['local_finish_rate']:.0%} | {row['escalation_rate']:.0%} | "
            f"${row['total_cloud_cost_usd']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Trial Detail",
            "",
            "| Repo | Task | Result | One-shot | Local finish | Attempts | Total sec |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for trial in report["trials"]:
        lines.append(
            f"| {trial['repo']} | {trial['task_id']} | {trial['result']} | "
            f"{'yes' if trial['verified_one_shot'] else 'no'} | "
            f"{'yes' if trial['verified_local_finish'] else 'no'} | "
            f"{trial['max_attempts']} | {trial['total_duration_sec']:.1f} |"
        )
    failures = [
        trial for trial in report["trials"]
        if trial.get("error") or trial.get("skipped_reason")
    ]
    if failures:
        lines.extend(
            [
                "",
                "## Exclusions / Infra Errors",
                "",
                "| Repo | Task | Result | Reason |",
                "|---|---|---|---|",
            ]
        )
        for trial in failures:
            reason = _one_line(trial.get("error") or trial.get("skipped_reason") or "")
            lines.append(f"| {trial['repo']} | {trial['task_id']} | {trial['result']} | {reason} |")
    lines.append("")
    return "\n".join(lines)


def _one_line(value: str) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


if __name__ == "__main__":
    raise SystemExit(main())
