from __future__ import annotations

import os
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from orchestrator.db.sqlite import (
    AuditRepository,
    GitAppInstallationRepository,
    IntegrationCredential,
    IntegrationRepository,
    RunEventRepository,
    TicketRepository,
)
from orchestrator.execution_safety import GitWorkspaceGuard
from orchestrator.git_flow import now_iso
from orchestrator.integration_notifications import post_slack_integration, slack_pr_payload
from orchestrator.models.ticket import Ticket, TicketStatus
from orchestrator.redaction import redact_text


ProviderName = Literal["github", "gitlab"]


@dataclass(frozen=True)
class PullRequestResult:
    pr_url: str
    status: str
    provider: ProviderName
    branch: str


@dataclass(frozen=True)
class ProviderPullRequestResult:
    pr_url: str
    status: str


@dataclass(frozen=True)
class RemoteRepository:
    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class PullRequestFlowError(RuntimeError):
    """Base error for PR flow failures."""


class AcceptanceGateError(PullRequestFlowError):
    """Raised when a ticket is not eligible for PR output."""


class DirtyWorkspaceError(PullRequestFlowError):
    """Raised when the project worktree has uncommitted changes."""


class MissingIntegrationError(PullRequestFlowError):
    """Raised when no PR-capable integration is configured."""


class PullRequestProvider(Protocol):
    name: ProviderName

    def push_branch(self, branch: str) -> None:
        ...

    def open_or_update_pr(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
        existing_pr_url: str | None = None,
    ) -> ProviderPullRequestResult:
        ...


@dataclass(frozen=True)
class ResolvedGitCredential:
    provider: ProviderName
    token: str
    credential_type: Literal["pat", "app"]
    credential_id: str


class GitCredential(Protocol):
    provider: ProviderName
    credential_id: str
    credential_type: Literal["pat", "app"]

    def resolve_token(self) -> ResolvedGitCredential:
        ...


class AppTokenMinter(Protocol):
    def mint_installation_token(self, provider: ProviderName, app_payload: dict[str, Any]) -> str:
        ...


class MissingAppTokenMinter(PullRequestFlowError):
    """Raised when an App credential is selected but no minter is configured."""


class PatGitCredential:
    provider: ProviderName
    credential_type: Literal["pat"] = "pat"

    def __init__(self, credential: IntegrationCredential, integrations: IntegrationRepository) -> None:
        self.credential = credential
        self.integrations = integrations
        self.provider = credential.provider  # type: ignore[assignment]
        self.credential_id = credential.id

    def resolve_token(self) -> ResolvedGitCredential:
        return ResolvedGitCredential(
            provider=self.provider,
            token=self.integrations.decrypted_token(self.credential.provider, self.credential.id),
            credential_type="pat",
            credential_id=self.credential.id,
        )


class AppGitCredential:
    provider: ProviderName
    credential_type: Literal["app"] = "app"

    def __init__(
        self,
        credential: IntegrationCredential,
        integrations: IntegrationRepository,
        minter: AppTokenMinter | None,
        audit: AuditRepository,
        installations: GitAppInstallationRepository | None = None,
    ) -> None:
        self.credential = credential
        self.integrations = integrations
        self.minter = minter
        self.audit = audit
        self.installations = installations
        self.provider = credential.provider  # type: ignore[assignment]
        self.credential_id = credential.id

    def resolve_token(self) -> ResolvedGitCredential:
        if self.minter is None:
            raise MissingAppTokenMinter("Git App credential selected but no token minter is configured")
        payload = self._app_payload()
        token = self.minter.mint_installation_token(self.provider, payload)
        self.audit.append(
            actor_id="control-plane",
            workspace_id=str(payload.get("workspace_id") or "default"),
            action="git.app_token.mint",
            target=self.credential.id,
            payload={"provider": self.provider, "installation_id": payload.get("installation_id")},
        )
        return ResolvedGitCredential(
            provider=self.provider,
            token=token,
            credential_type="app",
            credential_id=self.credential.id,
        )

    def _app_payload(self) -> dict[str, Any]:
        raw = self.integrations.decrypted_token(self.credential.provider, self.credential.id)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"installation_id": raw}
        if not isinstance(payload, dict):
            payload = {"installation_id": str(payload)}
        workspace_id = str(payload.get("workspace_id") or "default")
        account = payload.get("account")
        if self.installations is not None:
            record = self.installations.get(
                workspace_id=workspace_id,
                provider=self.provider,
                account=str(account) if isinstance(account, str) and account else None,
            )
            if record is not None:
                merged = dict(record.payload)
                merged.update(payload)
                merged["workspace_id"] = record.workspace_id
                merged["account"] = record.account
                merged["installation_id"] = record.installation_id
                return merged
        return payload


class PullRequestService:
    def __init__(
        self,
        *,
        repository: TicketRepository,
        integrations: IntegrationRepository,
        run_events: RunEventRepository,
        repo_root: str | Path,
        base_branch: str = "main",
        workspace_guard: GitWorkspaceGuard | None = None,
        provider_factory: Any | None = None,
        git_credential_factory: Any | None = None,
        app_token_minter: AppTokenMinter | None = None,
        app_installations: GitAppInstallationRepository | None = None,
    ) -> None:
        self.repository = repository
        self.integrations = integrations
        self.run_events = run_events
        self.repo_root = Path(repo_root).resolve()
        self.base_branch = base_branch
        self.workspace_guard = workspace_guard or GitWorkspaceGuard(self.repo_root)
        self.provider_factory = provider_factory or create_pull_request_provider
        self.audit = AuditRepository(repository.connection)
        self.app_token_minter = app_token_minter
        self.app_installations = app_installations
        self.git_credential_factory = git_credential_factory or self._create_git_credential

    def has_pr_integration(self) -> bool:
        return self._select_credential() is not None

    def open_or_update_pr(self, ticket_id: str) -> PullRequestResult:
        ticket = self._require_ticket(ticket_id)
        project_id = _ticket_project_id(ticket)
        token: str | None = None
        try:
            self._enforce_acceptance_gate(ticket)
            if self.workspace_guard.is_dirty():
                raise DirtyWorkspaceError(
                    "Cannot open PR: repository has uncommitted changes"
                )
            git_credential = self._select_credential()
            if git_credential is None:
                raise MissingIntegrationError("No github or gitlab integration is configured")

            resolved = git_credential.resolve_token()
            token = resolved.token
            provider = self.provider_factory(
                resolved.provider,
                token,
                self.repo_root,
            )
            branch = pr_branch_name(ticket)
            self._prepare_branch(ticket, branch)
            provider.push_branch(branch)
            metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
            provider_result = provider.open_or_update_pr(
                branch=branch,
                base_branch=_base_branch(ticket, self.base_branch),
                title=f"{ticket.id}: {ticket.title}",
                body=_pr_body(ticket),
                existing_pr_url=_string_or_none(metadata.get("pr_url")),
            )
            result = PullRequestResult(
                pr_url=provider_result.pr_url,
                status=provider_result.status,
                provider=resolved.provider,
                branch=branch,
            )
            updated = self._store_result(ticket_id, result)
            self.run_events.append_run_event(
                project_id=project_id,
                requirement_id=_ticket_requirement_id(updated),
                ticket_id=ticket_id,
                run_id=_ticket_run_id(updated),
                event_type="report",
                payload={
                    "stage": "pr",
                    "provider": result.provider,
                    "branch": result.branch,
                    "status": result.status,
                    "pr_url": result.pr_url,
                    "credential_type": resolved.credential_type,
                },
            )
            self.audit.append(
                actor_id="control-plane",
                workspace_id=project_id,
                action="git.pr.open",
                target=ticket_id,
                payload={
                    "provider": result.provider,
                    "credential_type": resolved.credential_type,
                    "credential_id": resolved.credential_id,
                    "pr_url": result.pr_url,
                },
            )
            post_slack_integration(
                self.repository.connection,
                project_id=project_id,
                ticket_id=ticket_id,
                run_id=_ticket_run_id(updated),
                payload=slack_pr_payload(
                    ticket_id=updated.id,
                    title=updated.title,
                    status=result.status,
                    pr_url=result.pr_url,
                ),
            )
            return result
        except PullRequestFlowError as exc:
            self._record_error(project_id, ticket_id, exc, token)
            raise
        except Exception as exc:  # noqa: BLE001 - external git/API failures become flow errors.
            wrapped = PullRequestFlowError(
                redact_text(str(exc), extra_secrets=[token] if token else None)
            )
            self._record_error(project_id, ticket_id, wrapped, token)
            raise wrapped from exc

    def _select_credential(self) -> GitCredential | None:
        for provider in ("github", "gitlab"):
            credentials = self.integrations.list(provider)
            if credentials:
                return self.git_credential_factory(credentials[0])
        return None

    def _create_git_credential(self, credential: IntegrationCredential) -> GitCredential:
        if _is_app_credential(credential):
            return AppGitCredential(
                credential,
                self.integrations,
                self.app_token_minter,
                self.audit,
                self.app_installations,
            )
        return PatGitCredential(credential, self.integrations)

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.repository.get(ticket_id)
        if ticket is None:
            raise KeyError(f"Ticket not found: {ticket_id}")
        return ticket

    def _enforce_acceptance_gate(self, ticket: Ticket) -> None:
        if ticket.result is None or ticket.result.outcome != "success":
            raise AcceptanceGateError("Ticket is not PR-eligible: DoD has not passed")
        if ticket.audit.verdict != "approved":
            raise AcceptanceGateError(
                "Ticket is not PR-eligible: gatekeeper has not approved it"
            )
        metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
        accepted = bool(metadata.get("accepted_at") or metadata.get("accepted_by"))
        if TicketStatus(ticket.status) != TicketStatus.AWAITING_ACCEPTANCE and not accepted:
            raise AcceptanceGateError(
                "Ticket is not PR-eligible: it must be awaiting acceptance or accepted by the PO"
            )

    def _prepare_branch(self, ticket: Ticket, branch: str) -> None:
        source_ref = _source_ref(ticket)
        current_branch = _git(
            self.repo_root,
            ["git", "branch", "--show-current"],
        ).stdout.strip()
        try:
            if _branch_exists(self.repo_root, branch):
                _git(self.repo_root, ["git", "checkout", branch])
                if source_ref != branch:
                    _git(self.repo_root, ["git", "merge", "--ff-only", source_ref])
            else:
                _git(self.repo_root, ["git", "checkout", "-b", branch, source_ref])
        finally:
            if current_branch and current_branch != branch:
                _git(self.repo_root, ["git", "checkout", current_branch])

    def _store_result(self, ticket_id: str, result: PullRequestResult) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        ticket_json = ticket.to_dict()
        metadata = ticket_json.setdefault("metadata", {})
        metadata["pr_url"] = result.pr_url
        metadata["pr_status"] = result.status
        metadata["pr_provider"] = result.provider
        metadata["pr_branch"] = result.branch
        metadata["pr_updated_at"] = now_iso()
        return self.repository.save(Ticket.from_dict(ticket_json))

    def _record_error(
        self,
        project_id: str,
        ticket_id: str,
        exc: Exception,
        token: str | None,
    ) -> None:
        self.run_events.append_run_event(
            project_id=project_id,
            ticket_id=ticket_id,
            run_id=_ticket_run_id(self.repository.get(ticket_id)),
            event_type="error",
            payload={
                "stage": "pr",
                "error": redact_text(str(exc), extra_secrets=[token] if token else None),
            },
        )


class BaseProvider:
    name: ProviderName

    def __init__(
        self,
        *,
        token: str,
        repo_root: str | Path,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.repo_root = Path(repo_root).resolve()
        self.remote = remote_repository(self.repo_root)
        self.http = http_client or httpx.Client(timeout=15.0)

    def push_branch(self, branch: str) -> None:
        env = _git_env_with_token(self.token)
        completed = subprocess.run(
            ["git", "push", "origin", f"{branch}:{branch}"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            env=env,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Git push failed: "
                + redact_text(completed.stderr.strip(), extra_secrets=[self.token])
            )


class GitHubPullRequestProvider(BaseProvider):
    name: ProviderName = "github"

    def open_or_update_pr(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
        existing_pr_url: str | None = None,
    ) -> ProviderPullRequestResult:
        headers = _bearer_headers(self.token)
        existing = self.http.get(
            f"https://api.github.com/repos/{self.remote.full_name}/pulls",
            headers=headers,
            params={
                "state": "open",
                "head": f"{self.remote.owner}:{branch}",
                "base": base_branch,
            },
        )
        existing.raise_for_status()
        pulls = existing.json()
        if pulls:
            number = pulls[0]["number"]
            updated = self.http.patch(
                f"https://api.github.com/repos/{self.remote.full_name}/pulls/{number}",
                headers=headers,
                json={"title": title, "body": body, "base": base_branch},
            )
            updated.raise_for_status()
            return ProviderPullRequestResult(
                pr_url=updated.json().get("html_url") or pulls[0]["html_url"],
                status="updated",
            )

        created = self.http.post(
            f"https://api.github.com/repos/{self.remote.full_name}/pulls",
            headers=headers,
            json={"title": title, "head": branch, "base": base_branch, "body": body},
        )
        created.raise_for_status()
        return ProviderPullRequestResult(
            pr_url=created.json().get("html_url") or existing_pr_url or "",
            status="opened",
        )


class GitLabPullRequestProvider(BaseProvider):
    name: ProviderName = "gitlab"

    def open_or_update_pr(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
        existing_pr_url: str | None = None,
    ) -> ProviderPullRequestResult:
        headers = {"PRIVATE-TOKEN": self.token}
        project = quote(self.remote.full_name, safe="")
        existing = self.http.get(
            f"https://{self.remote.host}/api/v4/projects/{project}/merge_requests",
            headers=headers,
            params={
                "state": "opened",
                "source_branch": branch,
                "target_branch": base_branch,
            },
        )
        existing.raise_for_status()
        merge_requests = existing.json()
        if merge_requests:
            iid = merge_requests[0]["iid"]
            updated = self.http.put(
                f"https://{self.remote.host}/api/v4/projects/{project}/merge_requests/{iid}",
                headers=headers,
                json={"title": title, "description": body, "target_branch": base_branch},
            )
            updated.raise_for_status()
            return ProviderPullRequestResult(
                pr_url=updated.json().get("web_url") or merge_requests[0]["web_url"],
                status="updated",
            )

        created = self.http.post(
            f"https://{self.remote.host}/api/v4/projects/{project}/merge_requests",
            headers=headers,
            json={
                "title": title,
                "source_branch": branch,
                "target_branch": base_branch,
                "description": body,
            },
        )
        created.raise_for_status()
        return ProviderPullRequestResult(
            pr_url=created.json().get("web_url") or existing_pr_url or "",
            status="opened",
        )


def create_pull_request_provider(
    provider: ProviderName,
    token: str,
    repo_root: str | Path,
) -> PullRequestProvider:
    if provider == "github":
        return GitHubPullRequestProvider(token=token, repo_root=repo_root)
    if provider == "gitlab":
        return GitLabPullRequestProvider(token=token, repo_root=repo_root)
    raise ValueError(f"Unsupported PR provider: {provider}")


def _is_app_credential(credential: IntegrationCredential) -> bool:
    scope_markers = {scope.lower() for scope in credential.scopes}
    if {"app", "github_app", "gitlab_app", "credential:app"}.intersection(scope_markers):
        return True
    return False


def pr_branch_name(ticket: Ticket) -> str:
    safe_ticket_id = re.sub(r"[^A-Za-z0-9_.-]", "-", ticket.id)
    slug = _slug(ticket.title)
    return f"haao/{safe_ticket_id}-{slug}"


def remote_repository(repo_root: str | Path) -> RemoteRepository:
    remote_url = _git(Path(repo_root), ["git", "config", "--get", "remote.origin.url"]).stdout.strip()
    match = re.match(r"https?://([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if not match:
        match = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", remote_url)
    if not match:
        match = re.match(r"ssh://git@([^/]+)/(.+?)(?:\.git)?$", remote_url)
    if not match:
        raise ValueError("Could not infer repository owner/name from remote.origin.url")
    host = match.group(1)
    path = match.group(2).removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError("Remote origin must include owner and repository name")
    return RemoteRepository(host=host, owner="/".join(parts[:-1]), name=parts[-1])


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-") or "ticket"


def _source_ref(ticket: Ticket) -> str:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    for key in ("git_commit", "git_branch"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "HEAD"


def _base_branch(ticket: Ticket, default: str) -> str:
    metadata = ticket.metadata.model_dump(mode="json") if ticket.metadata else {}
    value = metadata.get("git_base_branch")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _pr_body(ticket: Ticket) -> str:
    criteria = "\n".join(f"- {item}" for item in ticket.definition_of_done.acceptance_criteria)
    return (
        f"Ticket: {ticket.id}\n\n"
        f"{ticket.task.description}\n\n"
        "Acceptance criteria:\n"
        f"{criteria or '- See ticket definition of done.'}\n"
    )


def _ticket_project_id(ticket: Ticket) -> str:
    if ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        project_id = metadata.get("project_id")
        if isinstance(project_id, str) and project_id:
            return project_id
    return "default"


def _ticket_requirement_id(ticket: Ticket | None) -> str | None:
    if ticket is not None and ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        requirement_id = metadata.get("requirement_id")
        if isinstance(requirement_id, str) and requirement_id:
            return requirement_id
    return None


def _ticket_run_id(ticket: Ticket | None) -> str | None:
    if ticket is not None and ticket.metadata is not None:
        metadata = ticket.metadata.model_dump(mode="json")
        run_id = metadata.get("last_run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None


def _branch_exists(repo_root: Path, branch: str) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.returncode == 0


def _git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=repo_root,
        capture_output=True,
        text=True,
        shell=False,
        env=_git_env(),
    )
    if completed.returncode != 0:
        raise RuntimeError("Git command failed: " + " ".join(args) + "\n" + completed.stderr.strip())
    return completed


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "HAAO"),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "haao@example.local"),
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "HAAO"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "haao@example.local"),
    }


def _git_env_with_token(token: str) -> dict[str, str]:
    return {
        **_git_env(),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: bearer {token}",
    }


def _bearer_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
