from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from statistics import median
from typing import Any, Literal

from orchestrator.db.sqlite import RequirementRepository, TicketRepository
from orchestrator.models.requirement import Requirement
from orchestrator.models.ticket import Ticket
from orchestrator.supply_chain import build_supply_chain_signal


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
SENSITIVE_PATH_HINTS = (
    ".env",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "token",
    "private_key",
    "id_rsa",
    "config",
)


def build_ticket_signals(ticket: Ticket | dict, *, requirement: Requirement | dict | None = None) -> dict:
    payload = _ticket_dict(ticket)
    requirement_payload = _requirement_dict(requirement)
    task = _dict(payload.get("task"))
    context = _dict(payload.get("context"))
    dod = _dict(payload.get("definition_of_done"))
    execution = _dict(payload.get("execution"))

    affected_files = _affected_files(task, context)
    privacy_flags = _privacy_flags(
        affected_files=affected_files,
        context=context,
        assigned_model=str(execution.get("assigned_model") or ""),
        requirement=requirement_payload,
    )
    dod_strength = _dod_strength(dod)
    risk = _risk(affected_files=affected_files, context=context, dod_strength=dod_strength, privacy_flags=privacy_flags, ticket=payload)
    return {
        "ticket_id": payload.get("id"),
        "affected_files": affected_files,
        "risk": risk,
        "dod_strength": dod_strength,
        "cloud_privacy_flags": privacy_flags,
        "derived_only": True,
    }


def build_requirement_signals(requirement: Requirement) -> dict:
    tickets = [
        build_ticket_signals(ticket, requirement=requirement)
        for ticket in requirement.proposed_tickets
        if isinstance(ticket, dict)
    ]
    risk_order = {"low": 0, "medium": 1, "high": 2}
    highest_risk = "low"
    weak_dod_count = 0
    affected: set[str] = set()
    flags: list[dict] = []
    for item in tickets:
        risk_level = str(_dict(item.get("risk")).get("level") or "low")
        if risk_order.get(risk_level, 0) > risk_order.get(highest_risk, 0):
            highest_risk = risk_level
        if _dict(item.get("dod_strength")).get("level") == "weak":
            weak_dod_count += 1
        affected.update(str(path) for path in item.get("affected_files", []))
        flags.extend(_dict(flag) for flag in item.get("cloud_privacy_flags", []))
    return {
        "requirement_id": requirement.id,
        "project_id": requirement.project_id or "default",
        "ticket_count": len(tickets),
        "affected_files": sorted(affected),
        "highest_risk": highest_risk,
        "weak_dod_count": weak_dod_count,
        "cloud_privacy_flags": _dedupe_flags(flags),
        "tickets": tickets,
        "derived_only": True,
    }


def build_acceptance_summary(ticket: Ticket) -> dict:
    payload = ticket.to_dict()
    metadata = _dict(payload.get("metadata"))
    result = _dict(payload.get("result"))
    audit = _dict(payload.get("audit"))
    signals = build_ticket_signals(ticket)
    status = str(payload.get("status") or "")
    dod_passed = result.get("outcome") == "success"
    audit_approved = audit.get("verdict") == "approved"
    awaiting_or_done = status in {"awaiting_acceptance", "done"}
    diff_present = bool(str(result.get("diff") or "").strip())
    pr_url = _string_or_none(metadata.get("pr_url"))
    pr_ready = dod_passed and audit_approved and awaiting_or_done
    supply_chain = metadata.get("supply_chain")
    if not isinstance(supply_chain, dict):
        supply_chain = build_supply_chain_signal(str(result.get("diff") or ""))
    checks = [
        _check("dod_passed", "Definition of Done passed", dod_passed, "critical", str(result.get("outcome") or "pending")),
        _check("gatekeeper_approved", "Gatekeeper approved", audit_approved, "critical", str(audit.get("verdict") or "pending")),
        _check("awaiting_acceptance", "Ticket is ready for PO acceptance", awaiting_or_done, "critical", status),
        _check("diff_available", "Diff is available for inspection", diff_present, "warning", "present" if diff_present else "missing"),
        _check(
            "pr_ready",
            "PR can be opened or updated",
            pr_ready,
            "warning",
            pr_url or ("eligible" if pr_ready else "not eligible yet"),
        ),
    ]
    failed_critical = [item for item in checks if item["severity"] == "critical" and not item["passed"]]
    risk_level = _dict(signals.get("risk")).get("level")
    recommendation: Literal["ready", "review_risk", "needs_work"]
    if failed_critical:
        recommendation = "needs_work"
    elif risk_level == "high":
        recommendation = "review_risk"
    else:
        recommendation = "ready"
    return {
        "ticket_id": ticket.id,
        "status": status,
        "recommendation": recommendation,
        "checks": checks,
        "signals": signals,
        "pr": {
            "url": pr_url,
            "status": _string_or_none(metadata.get("pr_status")),
            "branch": _string_or_none(metadata.get("pr_branch")),
            "provider": _string_or_none(metadata.get("pr_provider")),
            "ready": pr_ready,
        },
        "supply_chain": supply_chain,
        "derived_only": True,
    }


def build_decision_center(connection: sqlite3.Connection, *, project_id: str) -> dict:
    tickets = TicketRepository(connection, project_id=project_id).list(project_id=project_id)
    requirements = RequirementRepository(connection, project_id=project_id).list(project_id=project_id)
    groups = {
        "gate1_scope": {
            "id": "gate1_scope",
            "title": "Gate 1 scope approval",
            "items": [],
        },
        "gate2_acceptance": {
            "id": "gate2_acceptance",
            "title": "Gate 2 acceptance",
            "items": [],
        },
        "blocked": {
            "id": "blocked",
            "title": "Blocked tickets",
            "items": [],
        },
        "high_risk": {
            "id": "high_risk",
            "title": "High-risk open work",
            "items": [],
        },
    }
    for requirement in requirements:
        if requirement.status == "preview_ready":
            signals = build_requirement_signals(requirement)
            groups["gate1_scope"]["items"].append(
                {
                    "type": "requirement",
                    "id": requirement.id,
                    "project_id": project_id,
                    "title": _short_title(requirement.prompt),
                    "status": requirement.status,
                    "priority": requirement.priority,
                    "signals": signals,
                    "actions": ["approve_scope", "edit_scope", "discard"],
                }
            )
    for ticket in tickets:
        if _is_abandoned(ticket):
            continue
        metadata = _ticket_metadata(ticket)
        signals = build_ticket_signals(ticket)
        item = _ticket_item(ticket, signals)
        if ticket.status == "backlog" and metadata.get("needs_approval") is not False:
            groups["gate1_scope"]["items"].append({**item, "actions": ["approve", "split", "abandon", "assign_model"]})
        if ticket.status == "awaiting_acceptance":
            groups["gate2_acceptance"]["items"].append(
                {**item, "acceptance_summary": build_acceptance_summary(ticket), "actions": ["accept", "reject", "split", "abandon"]}
            )
        if ticket.status == "blocked":
            groups["blocked"]["items"].append({**item, "actions": ["retry", "split", "assign_model", "abandon", "escalate"]})
        if ticket.status not in {"done", "abandoned", "split"} and _dict(signals.get("risk")).get("level") == "high":
            groups["high_risk"]["items"].append({**item, "actions": ["review_scope", "split", "assign_model"]})
    group_list = list(groups.values())
    return {
        "project_id": project_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "groups": group_list,
        "counts": {group["id"]: len(group["items"]) for group in group_list},
        "derived_only": True,
    }


def time_to_first_pr(tickets: list[dict], *, since: datetime | None = None) -> dict:
    durations: list[float] = []
    samples: list[dict] = []
    for ticket in tickets:
        metadata = _dict(ticket.get("metadata"))
        pr_at = _parse_datetime(metadata.get("pr_created_at")) or _parse_datetime(metadata.get("pr_updated_at"))
        created_at = _parse_datetime(metadata.get("created_at"))
        if pr_at is None or created_at is None or pr_at < created_at:
            continue
        if since is not None and pr_at < since:
            continue
        hours = round((pr_at - created_at).total_seconds() / 3600, 4)
        durations.append(hours)
        samples.append(
            {
                "ticket_id": ticket.get("id"),
                "hours": hours,
                "pr_url": _string_or_none(metadata.get("pr_url")),
                "opened_at": pr_at.isoformat(),
            }
        )
    return {
        "sample_size": len(durations),
        "avg_hours": round(sum(durations) / len(durations), 4) if durations else 0.0,
        "median_hours": round(float(median(durations)), 4) if durations else 0.0,
        "samples": samples,
    }


def roi_summary(*, tickets: list[dict], cost_total_usd: float, since: datetime | None = None) -> dict:
    done_tickets = 0
    accepted_tickets = 0
    for ticket in tickets:
        metadata = _dict(ticket.get("metadata"))
        done_at = _parse_datetime(metadata.get("accepted_at")) or _parse_datetime(metadata.get("git_merged_at")) or _parse_datetime(metadata.get("updated_at"))
        if ticket.get("status") != "done" or done_at is None:
            continue
        if since is not None and done_at < since:
            continue
        done_tickets += 1
        if metadata.get("accepted_at") or metadata.get("accepted_by"):
            accepted_tickets += 1
    estimated_hours_saved = round(done_tickets * 1.5, 2)
    assumed_hourly_rate_usd = 100.0
    estimated_value_usd = round(estimated_hours_saved * assumed_hourly_rate_usd, 2)
    net_value_usd = round(estimated_value_usd - cost_total_usd, 2)
    return {
        "done_tickets": done_tickets,
        "accepted_tickets": accepted_tickets,
        "estimated_hours_saved": estimated_hours_saved,
        "assumed_hours_saved_per_done_ticket": 1.5,
        "assumed_hourly_rate_usd": assumed_hourly_rate_usd,
        "estimated_value_usd": estimated_value_usd,
        "cloud_cost_usd": round(cost_total_usd, 4),
        "estimated_net_value_usd": net_value_usd,
        "method": "Heuristic: done tickets * 1.5 engineering hours at $100/hour minus tracked cloud cost.",
    }


def _affected_files(task: dict, context: dict) -> list[str]:
    files = {str(path) for path in task.get("target_files", []) if str(path).strip()}
    for file in context.get("files", []):
        if isinstance(file, dict) and file.get("path"):
            files.add(str(file["path"]))
    return sorted(files)


def _dod_strength(dod: dict) -> dict:
    tests = [item for item in dod.get("tests", []) if isinstance(item, dict) and str(item.get("command") or "").strip()]
    static_checks = [item for item in dod.get("static_checks", []) if str(item).strip()]
    acceptance = [item for item in dod.get("acceptance_criteria", []) if str(item).strip()]
    targeted = sum(1 for item in tests if any(marker in str(item.get("command", "")) for marker in ("::", " -q ", "pytest ", "npm test", "pnpm test", "cargo test", "go test")))
    score = len(tests) * 2 + len(static_checks) + min(len(acceptance), 2) + min(targeted, 2)
    if score >= 5:
        level = "strong"
    elif score >= 2:
        level = "medium"
    else:
        level = "weak"
    return {
        "level": level,
        "score": score,
        "tests_count": len(tests),
        "static_checks_count": len(static_checks),
        "acceptance_criteria_count": len(acceptance),
        "machine_verifiable": bool(tests or static_checks),
        "reasons": _dod_reasons(tests, static_checks, acceptance),
    }


def _dod_reasons(tests: list[dict], static_checks: list[object], acceptance: list[object]) -> list[str]:
    reasons: list[str] = []
    if tests:
        reasons.append(f"{len(tests)} executable DoD test(s)")
    else:
        reasons.append("No executable DoD tests")
    if static_checks:
        reasons.append(f"{len(static_checks)} static check(s)")
    if acceptance:
        reasons.append(f"{len(acceptance)} acceptance criterion/criteria")
    return reasons


def _privacy_flags(*, affected_files: list[str], context: dict, assigned_model: str, requirement: dict | None) -> list[dict]:
    flags: list[dict] = []
    if _is_cloud_model(assigned_model):
        flags.append(_flag("cloud_execution_model", "warning", f"Assigned model is cloud-routed: {assigned_model}"))
    if any(_sensitive_path(path) for path in affected_files):
        flags.append(_flag("sensitive_file_target", "warning", "Target or context path looks security/privacy-sensitive"))
    if any(isinstance(file, dict) and file.get("truncated") for file in context.get("files", [])):
        flags.append(_flag("truncated_context", "info", "One or more context files were truncated"))
    if requirement:
        attachments = requirement.get("attachments", [])
        if any(isinstance(item, dict) and item.get("type") == "image" for item in attachments):
            flags.append(_flag("image_attachment_cloud_analysis", "warning", "Image attachments may be analyzed by the cloud Tech Lead"))
        if any(isinstance(item, dict) and item.get("type") == "file" for item in attachments):
            flags.append(_flag("file_attachment_context", "info", "File attachments are included in requirement context"))
    return _dedupe_flags(flags)


def _risk(*, affected_files: list[str], context: dict, dod_strength: dict, privacy_flags: list[dict], ticket: dict) -> dict:
    reasons: list[str] = []
    score = 0
    if len(affected_files) >= 4:
        score += 2
        reasons.append("Touches 4 or more files")
    elif len(affected_files) >= 2:
        score += 1
        reasons.append("Touches multiple files")
    if dod_strength.get("level") == "weak":
        score += 2
        reasons.append("Weak or missing machine-verifiable DoD")
    elif dod_strength.get("level") == "medium":
        score += 1
        reasons.append("Medium DoD strength")
    if not context.get("files"):
        score += 1
        reasons.append("No injected context files")
    if any(flag.get("severity") == "warning" for flag in privacy_flags):
        score += 1
        reasons.append("Cloud/privacy warning present")
    if _dict(ticket.get("task")).get("constraints") == []:
        score += 1
        reasons.append("No explicit constraints")
    if ticket.get("type") in {"refactor", "chore"} and len(affected_files) > 1:
        score += 1
        reasons.append("Broad maintenance-style change")
    if score >= 4:
        level = "high"
    elif score >= 2:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "score": score, "reasons": reasons or ["Narrow scope with checkable DoD"]}


def _ticket_item(ticket: Ticket, signals: dict) -> dict:
    metadata = _ticket_metadata(ticket)
    return {
        "type": "ticket",
        "id": ticket.id,
        "project_id": metadata.get("project_id") or "default",
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "requirement_id": metadata.get("requirement_id"),
        "signals": signals,
    }


def _check(check_id: str, label: str, passed: bool, severity: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "passed": passed, "severity": severity, "detail": detail}


def _flag(flag_id: str, severity: str, message: str) -> dict:
    return {"id": flag_id, "severity": severity, "message": message}


def _dedupe_flags(flags: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for flag in flags:
        key = str(flag.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(flag)
    return result


def _is_cloud_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(hint in lowered for hint in CLOUD_MODEL_HINTS) or (":" in model_id and not lowered.startswith("local:"))


def _sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in SENSITIVE_PATH_HINTS)


def _ticket_dict(ticket: Ticket | dict) -> dict:
    return ticket.to_dict() if isinstance(ticket, Ticket) else _dict(ticket)


def _requirement_dict(requirement: Requirement | dict | None) -> dict | None:
    if requirement is None:
        return None
    return requirement.to_dict() if isinstance(requirement, Requirement) else _dict(requirement)


def _ticket_metadata(ticket: Ticket) -> dict:
    return ticket.metadata.model_dump(mode="json") if ticket.metadata is not None else {}


def _is_abandoned(ticket: Ticket) -> bool:
    metadata = _ticket_metadata(ticket)
    return bool(ticket.status == "abandoned" or metadata.get("abandoned") or metadata.get("abandoned_at"))


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _short_title(text: str, *, limit: int = 90) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


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
