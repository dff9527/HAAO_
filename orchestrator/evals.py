from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid

from orchestrator.benchmark_runner import (
    aggregate_trial_results,
    load_manifest,
    run_requirement_trial,
)
from orchestrator.config import Settings
from orchestrator.db.sqlite import EvalRunRecord, EvalRunRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "benchmarks" / "r102_manifest.json"


@dataclass(frozen=True)
class EvalTaskSet:
    id: str
    label: str
    description: str
    task_ids: list[str]
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "task_ids": self.task_ids,
            "task_count": len(self.task_ids),
            "source": self.source,
        }


class EvalService:
    def __init__(
        self,
        repository: EvalRunRepository,
        *,
        manifest_path: str | Path = DEFAULT_MANIFEST,
    ) -> None:
        self.repository = repository
        self.manifest_path = Path(manifest_path)

    def list_task_sets(self) -> list[EvalTaskSet]:
        manifest = self._load_manifest()
        task_ids = [task.id for task in manifest.tasks]
        task_sets = [
            EvalTaskSet(
                id="r102-active",
                label="R-102 active",
                description="Reviewed R-102 benchmark tasks from the checked-in manifest.",
                task_ids=task_ids,
                source=str(self.manifest_path),
            )
        ]
        if task_ids:
            task_sets.append(
                EvalTaskSet(
                    id="r102-smoke",
                    label="R-102 smoke",
                    description="One checked-in R-102 task for a quick harness smoke test.",
                    task_ids=task_ids[:1],
                    source=str(self.manifest_path),
                )
            )
        return task_sets

    def start_run(
        self,
        *,
        model_id: str,
        task_set_id: str,
        trials: int = 1,
    ) -> EvalRunRecord:
        self._tasks_for_set(task_set_id)
        return self.repository.create(
            eval_id=f"EVAL-{uuid.uuid4().hex[:12]}",
            model_id=model_id,
            task_set_id=task_set_id,
            trials=max(1, min(int(trials), 10)),
        )

    def run_to_completion(self, eval_id: str, *, settings: Settings) -> EvalRunRecord:
        run = self.repository.get(eval_id)
        if run is None:
            raise KeyError(f"Eval run not found: {eval_id}")
        try:
            summary = self._run_summary(run, settings=settings)
            baseline = self.repository.latest_completed_before(
                model_id=run.model_id,
                task_set_id=run.task_set_id,
                started_before=run.started_at,
                exclude_id=run.id,
            )
            comparison = compare_to_baseline(summary, baseline)
            summary["baseline"] = comparison
            return self.repository.complete(
                run.id,
                summary=summary,
                baseline_run_id=comparison.get("run_id") if comparison else None,
                regressed=bool(comparison.get("regressed")) if comparison else False,
            )
        except Exception as exc:  # noqa: BLE001 - eval run should surface failures as run status
            return self.repository.fail(
                run.id,
                error=str(exc),
                summary={
                    "model_id": run.model_id,
                    "task_set_id": run.task_set_id,
                    "generated_at": _now(),
                },
            )

    def _run_summary(self, run: EvalRunRecord, *, settings: Settings) -> dict[str, Any]:
        manifest, tasks = self._tasks_for_set(run.task_set_id)
        trials = []
        for task in tasks:
            repo_def = manifest.repos[task.repo]
            for trial_number in range(1, max(1, run.trials) + 1):
                trials.append(
                    run_requirement_trial(
                        task=task,
                        repo_def=repo_def,
                        settings=settings,
                        trial=trial_number,
                        local_model=run.model_id,
                        max_output_tokens=settings.local_max_output_tokens,
                        patch_mode_threshold_tokens=settings.local_patch_mode_threshold_tokens,
                    )
                )

        metrics = aggregate_trial_results(trials)
        by_category: dict[str, list] = {}
        for trial in trials:
            by_category.setdefault(trial.category, []).append(trial)

        return {
            "benchmark": "R-102",
            "generated_at": _now(),
            "manifest": str(self.manifest_path.resolve()),
            "model_id": run.model_id,
            "task_set_id": run.task_set_id,
            "task_ids": [task.id for task in tasks],
            "trials_per_task": run.trials,
            "summary": _with_pass_rate(metrics),
            "by_category": {
                category: _with_pass_rate(aggregate_trial_results(items))
                for category, items in by_category.items()
            },
            "trials": [_trial_to_dict(trial) for trial in trials],
        }

    def _tasks_for_set(self, task_set_id: str):
        manifest = self._load_manifest()
        task_sets = {task_set.id: task_set for task_set in self.list_task_sets()}
        task_set = task_sets.get(task_set_id)
        if task_set is None:
            raise KeyError(f"Unknown eval task set: {task_set_id}")
        selected = [task for task in manifest.tasks if task.id in set(task_set.task_ids)]
        if not selected:
            raise ValueError(f"Eval task set has no tasks: {task_set_id}")
        return manifest, selected

    def _load_manifest(self):
        return load_manifest(self.manifest_path)


def compare_to_baseline(summary: dict[str, Any], baseline: EvalRunRecord | None) -> dict[str, Any]:
    current = _summary_metrics(summary)
    if baseline is None:
        return {
            "run_id": None,
            "regressed": False,
            "reason": "no_baseline",
            "current": current,
            "previous": None,
            "diff": {},
        }
    previous = _summary_metrics(baseline.summary or {})
    diff = {
        key: round(float(current.get(key, 0.0)) - float(previous.get(key, 0.0)), 4)
        for key in ("pass_rate", "one_shot_rate", "local_finish_rate", "escalation_rate")
    }
    regressed = diff["pass_rate"] < 0 or diff["one_shot_rate"] < 0
    return {
        "run_id": baseline.id,
        "regressed": regressed,
        "reason": "lower_pass_or_one_shot" if regressed else "no_regression",
        "current": current,
        "previous": previous,
        "diff": diff,
    }


def _summary_metrics(payload: dict[str, Any]) -> dict[str, float]:
    metrics = payload.get("summary") if isinstance(payload, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}
    pass_rate = float(metrics.get("pass_rate", metrics.get("local_finish_rate", 0.0)) or 0.0)
    return {
        "pass_rate": pass_rate,
        "one_shot_rate": float(metrics.get("one_shot_rate", 0.0) or 0.0),
        "local_finish_rate": float(metrics.get("local_finish_rate", pass_rate) or 0.0),
        "escalation_rate": float(metrics.get("escalation_rate", 0.0) or 0.0),
    }


def _with_pass_rate(metrics: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(metrics)
    enriched["pass_rate"] = enriched.get("local_finish_rate", 0.0)
    return enriched


def _trial_to_dict(trial) -> dict[str, Any]:
    payload = asdict(trial)
    payload["tickets"] = [asdict(snapshot) for snapshot in trial.tickets]
    return payload


def _now() -> str:
    return datetime.now(UTC).isoformat()
