#!/usr/bin/env python3
"""Wave 4/11 proof report: run R-102 tasks across trials and emit JSON + Markdown.

This script reuses the R-102 runner and does not modify benchmark assets. By default it
runs the full active task set in benchmarks/r102_manifest.json. Use --task-ids for a
small manual subset or --one-per-repo for the legacy Wave-4 smoke shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    parser = argparse.ArgumentParser(
        description="Wave 11 real-repo benchmark proof report",
        epilog=(
            "Manual headline run: PYTHONPATH=. python scripts/wave4_real_repo_benchmark_report.py "
            "--trials 3 --local-model qwen3-coder-next --out-json output/wave11_r102.json "
            "--out-md output/wave11_r102.md"
        ),
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--task-ids", default="", help="Comma-separated task ids. Defaults to all active tasks.")
    parser.add_argument(
        "--one-per-repo",
        action="store_true",
        help="Legacy smoke shape: select only the first task for each repo.",
    )
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
    tasks = _select_tasks(manifest, args.task_ids, one_per_repo=args.one_per_repo)
    if not tasks:
        parser.error("No benchmark tasks selected")

    settings = get_settings()
    if not settings.claude_api_key:
        parser.error("CLAUDE_API_KEY is not configured")
    max_output_tokens = args.max_output_tokens or settings.local_max_output_tokens
    patch_mode_threshold_tokens = args.patch_mode_threshold_tokens or settings.local_patch_mode_threshold_tokens

    report = build_report(
        manifest=manifest,
        manifest_path=args.manifest,
        tasks=tasks,
        settings=settings,
        trials_per_task=max(1, args.trials),
        local_model=args.local_model,
        local_timeout_sec=args.local_timeout,
        max_target_file_bytes=args.max_target_file_bytes,
        max_output_tokens=max_output_tokens,
        patch_mode_threshold_tokens=patch_mode_threshold_tokens,
    )

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out_json = Path(args.out_json or PROJECT_ROOT / "output" / f"wave4_real_repo_benchmark_{stamp}.json")
    out_md = Path(args.out_md or PROJECT_ROOT / "output" / f"wave4_real_repo_benchmark_{stamp}.md")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(_markdown_report(report), encoding="utf-8")
    print(f"JSON report written to {out_json}")
    print(f"Markdown report written to {out_md}")
    return 0


TrialRunner = Callable[..., Any]


def build_report(
    *,
    manifest,
    manifest_path: str | Path,
    tasks: list[Any],
    settings: Any,
    trials_per_task: int,
    local_model: str,
    local_timeout_sec: float,
    max_target_file_bytes: int,
    max_output_tokens: int,
    patch_mode_threshold_tokens: int,
    trial_runner: TrialRunner = run_requirement_trial,
    generated_at: str | None = None,
) -> dict:
    trials = []
    for task in tasks:
        repo_def = manifest.repos[task.repo]
        for trial_number in range(1, max(1, trials_per_task) + 1):
            print(f"[{task.repo}:{task.id}] trial {trial_number}/{trials_per_task} starting", flush=True)
            trial = trial_runner(
                task=task,
                repo_def=repo_def,
                settings=settings,
                trial=trial_number,
                local_model=local_model,
                local_timeout_sec=local_timeout_sec,
                max_target_file_bytes=max_target_file_bytes,
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

    by_repo = {
        repo: aggregate_trial_results([trial for trial in trials if trial.repo == repo])
        for repo in sorted({trial.repo for trial in trials})
    }
    by_task = {
        task.id: aggregate_trial_results([trial for trial in trials if trial.task_id == task.id])
        for task in tasks
    }
    return {
        "benchmark": "Wave-11-real-repo-proof",
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "manifest": str(Path(manifest_path).resolve()),
        "repos": sorted({task.repo for task in tasks}),
        "task_ids": [task.id for task in tasks],
        "task_count": len(tasks),
        "local_model": local_model,
        "cloud_model": settings.claude_model,
        "cost_status": _report_cost_status(trials),
        "trials_per_task": max(1, trials_per_task),
        "summary": aggregate_trial_results(trials),
        "by_repo": by_repo,
        "by_task": by_task,
        "trials": [_trial_to_dict(trial) for trial in trials],
    }


def _select_tasks(manifest, task_ids: str, *, one_per_repo: bool = False):  # type: ignore[no-untyped-def]
    selected_ids = {item.strip() for item in task_ids.split(",") if item.strip()}
    if selected_ids:
        return [task for task in manifest.tasks if task.id in selected_ids]
    if not one_per_repo:
        return list(manifest.tasks)
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


def _report_cost_status(trials: list[Any]) -> str:
    has_cloud_usage = any(
        trial.cloud_cost_usd > 0 or trial.cloud_input_tokens > 0 or trial.cloud_output_tokens > 0
        for trial in trials
    )
    return "estimated" if has_cloud_usage else "unknown"


def _trial_to_dict(trial) -> dict:  # type: ignore[no-untyped-def]
    payload = asdict(trial)
    payload["tickets"] = [asdict(snapshot) for snapshot in trial.tickets]
    return payload


def _markdown_report(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Wave 11 Real-Repo Benchmark Proof",
        "",
        f"Generated: {report['generated_at']}",
        f"Repos: {', '.join(report['repos'])}",
        f"Tasks: {report['task_count']} ({', '.join(report['task_ids'])})",
        f"Trials per task: {report['trials_per_task']}",
        f"Local model: `{report['local_model']}`",
        f"Cloud model: `{report['cloud_model']}`",
        f"Cost status: `{report['cost_status']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Trials counted | {summary['trials_counted']} / {summary['trials_total']} |",
        f"| Verified one-shot | {summary['one_shot']} ({_fmt_rate_with_range(summary, 'one_shot_rate')}) |",
        f"| Verified local finish | {summary['local_finish']} ({_fmt_rate_with_range(summary, 'local_finish_rate')}) |",
        f"| Escalated / blocked | {summary['escalated']} ({_fmt_rate_with_range(summary, 'escalation_rate')}) |",
        f"| Existing tests green | {summary['existing_tests_still_green']} ({_fmt_rate_with_range(summary, 'existing_tests_still_green_rate')}) |",
        f"| Median local inference | {summary['median_local_inference_sec']:.2f}s |",
        f"| Total cloud cost | ${summary['total_cloud_cost_usd']:.4f} |",
        "",
        "## By Task",
        "",
        "| Task | Counted | One-shot | Local finish | Escalation | Existing tests |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for task_id, row in report["by_task"].items():
        lines.append(
            f"| {task_id} | {row['trials_counted']} | {_fmt_rate_with_range(row, 'one_shot_rate')} | "
            f"{_fmt_rate_with_range(row, 'local_finish_rate')} | "
            f"{_fmt_rate_with_range(row, 'escalation_rate')} | "
            f"{_fmt_rate_with_range(row, 'existing_tests_still_green_rate')} |"
        )
    lines.extend(
        [
            "",
            "## By Repo",
            "",
            "| Repo | Counted | One-shot | Local finish | Escalation | Cloud cost |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for repo, row in report["by_repo"].items():
        lines.append(
            f"| {repo} | {row['trials_counted']} | {_fmt_rate_with_range(row, 'one_shot_rate')} | "
            f"{_fmt_rate_with_range(row, 'local_finish_rate')} | "
            f"{_fmt_rate_with_range(row, 'escalation_rate')} | "
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


def _fmt_rate_with_range(row: dict, metric: str) -> str:
    stats = row.get("trial_group_stats", {}).get(metric, {})
    value = row.get(metric, 0.0)
    if not stats or stats.get("n", 0) <= 1:
        return f"{value:.0%}"
    mean = float(stats.get("mean", value))
    low = float(stats.get("min", value))
    high = float(stats.get("max", value))
    return f"{value:.0%} (trial mean {mean:.0%}, range {low:.0%}-{high:.0%})"


if __name__ == "__main__":
    raise SystemExit(main())
