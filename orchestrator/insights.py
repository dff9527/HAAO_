from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Literal


InsightRange = Literal["7d", "30d", "all"]
COST_STATUSES = ("actual", "estimated", "unknown")
CLOUD_MODEL_HINTS = (
    "anthropic",
    "claude",
    "openai",
    "gpt",
    "gemini",
    "google",
    "openrouter",
    "together",
    "fireworks",
)


@dataclass(frozen=True)
class InsightEvent:
    id: int
    project_id: str
    requirement_id: str | None
    ticket_id: str | None
    run_id: str | None
    event_type: str
    ts: datetime
    model_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float
    cost_status: str
    payload: dict


class InsightsService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def aggregate(self, *, project_id: str, range_name: InsightRange) -> dict:
        since = _range_start(range_name)
        events = self._events(project_id, since=since)
        tickets = self._tickets(project_id)
        run_ids = _run_ids(events)
        finished_by_run = _finished_by_run(events)
        ticket_by_id = {ticket.get("id"): ticket for ticket in tickets}
        ticket_type_by_run = _ticket_type_by_run(events, ticket_by_id)

        throughput, cycle_time = _ticket_outcomes(tickets, since=since)
        cost = _cost_summary(events)
        local_vs_cloud = _local_vs_cloud(events)
        escalation_count = sum(1 for event in events if event.event_type == "escalation")
        runs_count = len(run_ids)

        return {
            "project_id": project_id,
            "range": range_name,
            "generated_at": datetime.now(UTC).isoformat(),
            "throughput": throughput,
            "cycle_time": cycle_time,
            "escalation_rate": {
                "runs": runs_count,
                "escalations": escalation_count,
                "rate": _ratio(escalation_count, runs_count),
            },
            "local_vs_cloud": local_vs_cloud,
            "cost": cost,
            "model_scorecard": _model_scorecard(
                events,
                finished_by_run=finished_by_run,
                ticket_by_id=ticket_by_id,
                ticket_type_by_run=ticket_type_by_run,
            ),
        }

    def _events(self, project_id: str, *, since: datetime | None) -> list[InsightEvent]:
        params: list[object] = [project_id]
        clause = "project_id = ?"
        if since is not None:
            clause += " AND ts >= ?"
            params.append(since.isoformat())
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM run_events
            WHERE {clause}
            ORDER BY id ASC
            """,
            params,
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def _tickets(self, project_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT ticket_json
            FROM tickets
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        tickets: list[dict] = []
        for row in rows:
            try:
                ticket = json.loads(row["ticket_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(ticket, dict):
                tickets.append(ticket)
        return tickets


def _ticket_outcomes(tickets: list[dict], *, since: datetime | None) -> tuple[dict, dict]:
    throughput_counter: Counter[str] = Counter()
    durations_hours: list[float] = []
    for ticket in tickets:
        if ticket.get("status") != "done":
            continue
        metadata = ticket.get("metadata") if isinstance(ticket.get("metadata"), dict) else {}
        done_at = _ticket_done_at(metadata)
        if done_at is None or (since is not None and done_at < since):
            continue
        throughput_counter[done_at.date().isoformat()] += 1
        created_at = _parse_datetime(metadata.get("created_at"))
        if created_at is not None and done_at >= created_at:
            durations_hours.append(round((done_at - created_at).total_seconds() / 3600, 4))

    return (
        {
            "total_done": sum(throughput_counter.values()),
            "series": [
                {"date": day, "count": throughput_counter[day]}
                for day in _series_days(throughput_counter)
            ],
        },
        {
            "sample_size": len(durations_hours),
            "avg_hours": round(sum(durations_hours) / len(durations_hours), 4)
            if durations_hours
            else 0.0,
            "median_hours": round(float(median(durations_hours)), 4)
            if durations_hours
            else 0.0,
        },
    )


def _cost_summary(events: list[InsightEvent]) -> dict:
    by_status = {status: 0.0 for status in COST_STATUSES}
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {status: 0.0 for status in COST_STATUSES})
    for event in events:
        if event.event_type != "model_call":
            continue
        status = event.cost_status if event.cost_status in COST_STATUSES else "unknown"
        by_status[status] = round(by_status[status] + event.cost_usd, 4)
        day = event.ts.date().isoformat()
        by_day[day][status] = round(by_day[day][status] + event.cost_usd, 4)

    series = []
    for day in sorted(by_day):
        bucket = {status: round(by_day[day][status], 4) for status in COST_STATUSES}
        series.append({"date": day, **bucket, "total_usd": round(sum(bucket.values()), 4)})
    return {
        "total_usd": round(sum(by_status.values()), 4),
        "by_status": by_status,
        "series": series,
    }


def _local_vs_cloud(events: list[InsightEvent]) -> dict:
    buckets = {
        "local": {"count": 0, "share": 0.0, "cost_usd": 0.0},
        "cloud": {"count": 0, "share": 0.0, "cost_usd": 0.0},
    }
    model_calls = [event for event in events if event.event_type == "model_call"]
    for event in model_calls:
        lane = _model_lane(event)
        buckets[lane]["count"] += 1
        buckets[lane]["cost_usd"] = round(buckets[lane]["cost_usd"] + event.cost_usd, 4)
    total = len(model_calls)
    for lane in ("local", "cloud"):
        buckets[lane]["share"] = _ratio(int(buckets[lane]["count"]), total)
    return {
        "total_model_calls": total,
        "local": buckets["local"],
        "cloud": buckets["cloud"],
    }


def _model_scorecard(
    events: list[InsightEvent],
    *,
    finished_by_run: dict[str, InsightEvent],
    ticket_by_id: dict[object, dict],
    ticket_type_by_run: dict[str, str],
) -> list[dict]:
    model_calls = [event for event in events if event.event_type == "model_call"]
    run_events_by_id: dict[str, list[InsightEvent]] = defaultdict(list)
    for event in events:
        if event.run_id:
            run_events_by_id[event.run_id].append(event)

    scorecards: dict[str, dict] = {}
    for event in model_calls:
        model_id = event.model_id or "unknown"
        card = scorecards.setdefault(
            model_id,
            {
                "model_id": model_id,
                "sample_size": 0,
                "runs": 0,
                "model_calls": 0,
                "successful_runs": 0,
                "success_rate": 0.0,
                "retries": 0,
                "escalations": 0,
                "cost_usd": 0.0,
                "cost_by_status": {status: 0.0 for status in COST_STATUSES},
                "task_type_mix": {},
                "human_override_count": 0,
                "_run_ids": set(),
            },
        )
        card["model_calls"] += 1
        card["cost_usd"] = round(card["cost_usd"] + event.cost_usd, 4)
        status = event.cost_status if event.cost_status in COST_STATUSES else "unknown"
        card["cost_by_status"][status] = round(card["cost_by_status"][status] + event.cost_usd, 4)
        if event.run_id:
            card["_run_ids"].add(event.run_id)

    for card in scorecards.values():
        run_ids = set(card.pop("_run_ids"))
        card["sample_size"] = len(run_ids)
        card["runs"] = len(run_ids)
        task_type_counter: Counter[str] = Counter()
        human_override_count = 0
        successful = 0
        retries = 0
        escalations = 0
        for run_id in run_ids:
            finished = finished_by_run.get(run_id)
            if finished is not None and bool(finished.payload.get("passed")):
                successful += 1
            retries += sum(1 for event in run_events_by_id.get(run_id, []) if event.event_type == "retry")
            escalations += sum(
                1 for event in run_events_by_id.get(run_id, []) if event.event_type == "escalation"
            )
            task_type_counter[ticket_type_by_run.get(run_id, "unknown")] += 1
            if _run_has_human_override(run_events_by_id.get(run_id, []), ticket_by_id):
                human_override_count += 1
        card["successful_runs"] = successful
        card["success_rate"] = _ratio(successful, len(run_ids))
        card["retries"] = retries
        card["escalations"] = escalations
        card["task_type_mix"] = dict(sorted(task_type_counter.items()))
        card["human_override_count"] = human_override_count

    return sorted(scorecards.values(), key=lambda item: item["model_id"])


def _run_has_human_override(events: list[InsightEvent], ticket_by_id: dict[object, dict]) -> bool:
    for event in events:
        payload = event.payload
        if event.event_type in {"retry", "escalation"} and payload.get("manual") is True:
            return True
        ticket = ticket_by_id.get(event.ticket_id)
        metadata = ticket.get("metadata", {}) if isinstance(ticket, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        if any(
            bool(metadata.get(key))
            for key in (
                "human_authored",
                "manual_retry",
                "approved_by",
                "accepted_by",
                "product_rejected_by",
                "diff_approved_at",
            )
        ):
            return True
        if ticket and ticket.get("created_by") == "human":
            return True
    return False


def _run_ids(events: list[InsightEvent]) -> set[str]:
    return {
        event.run_id
        for event in events
        if event.run_id and event.event_type in {"run_started", "run_finished", "model_call"}
    }


def _finished_by_run(events: list[InsightEvent]) -> dict[str, InsightEvent]:
    return {
        event.run_id: event
        for event in events
        if event.run_id and event.event_type == "run_finished"
    }


def _ticket_type_by_run(events: list[InsightEvent], ticket_by_id: dict[object, dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for event in events:
        if not event.run_id or not event.ticket_id or event.run_id in result:
            continue
        ticket = ticket_by_id.get(event.ticket_id)
        ticket_type = ticket.get("type") if isinstance(ticket, dict) else None
        result[event.run_id] = ticket_type if isinstance(ticket_type, str) else "unknown"
    return result


def _model_lane(event: InsightEvent) -> Literal["local", "cloud"]:
    if event.payload.get("used_cloud_usage") is True:
        return "cloud"
    if event.cost_status in {"actual", "estimated"} and event.cost_usd > 0:
        return "cloud"
    model_id = (event.model_id or "").lower()
    if any(hint in model_id for hint in CLOUD_MODEL_HINTS):
        return "cloud"
    return "local"


def _ticket_done_at(metadata: dict) -> datetime | None:
    for key in ("accepted_at", "git_merged_at", "updated_at"):
        value = _parse_datetime(metadata.get(key))
        if value is not None:
            return value
    return None


def _event_from_row(row: sqlite3.Row) -> InsightEvent:
    payload = {}
    if row["payload_json"]:
        try:
            decoded = json.loads(row["payload_json"])
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = {}
    return InsightEvent(
        id=int(row["id"]),
        project_id=row["project_id"],
        requirement_id=row["requirement_id"],
        ticket_id=row["ticket_id"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        ts=_parse_datetime(row["ts"]) or datetime.now(UTC),
        model_id=row["model_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=float(row["cost_usd"] or 0.0),
        cost_status=row["cost_status"] or "unknown",
        payload=payload,
    )


def _range_start(range_name: InsightRange) -> datetime | None:
    now = datetime.now(UTC)
    if range_name == "7d":
        return now - timedelta(days=7)
    if range_name == "30d":
        return now - timedelta(days=30)
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _series_days(counter: Counter[str]) -> list[str]:
    return sorted(counter)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
