from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from orchestrator.config import Settings
from orchestrator.pr_flow import ProviderName


class AppTokenMintError(RuntimeError):
    """Raised when a Git App installation token cannot be minted."""


@dataclass(frozen=True)
class CachedAppToken:
    token: str
    expires_at: datetime


class RealAppTokenMinter:
    """Mint short-lived Git provider tokens behind pr_flow.AppTokenMinter."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        now: Any | None = None,
    ) -> None:
        self.settings = settings
        self._http_client = http_client
        self._owns_client = http_client is None
        self._now = now or (lambda: datetime.now(UTC))
        self._cache: dict[tuple[str, str], CachedAppToken] = {}

    def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def mint_installation_token(self, provider: ProviderName, app_payload: dict[str, Any]) -> str:
        cache_key = _cache_key(provider, app_payload)
        cached = self._cache.get(cache_key)
        if cached is not None and cached.expires_at - self._now() > timedelta(seconds=60):
            return cached.token
        if provider == "github":
            token, expires_at = self._mint_github(app_payload)
        elif provider == "gitlab":
            token, expires_at = self._mint_gitlab(app_payload)
        else:
            raise AppTokenMintError(f"Unsupported App provider: {provider}")
        self._cache[cache_key] = CachedAppToken(token=token, expires_at=expires_at)
        return token

    def revoke_cached_token(self, provider: ProviderName, app_payload: dict[str, Any]) -> None:
        self._cache.pop(_cache_key(provider, app_payload), None)

    def _mint_github(self, app_payload: dict[str, Any]) -> tuple[str, datetime]:
        app_id = str(app_payload.get("app_id") or self.settings.github_app_id).strip()
        private_key = str(app_payload.get("private_key") or self.settings.github_app_private_key).strip()
        installation_id = str(app_payload.get("installation_id") or "").strip()
        if not app_id:
            raise AppTokenMintError("GITHUB_APP_ID is not configured")
        if not private_key:
            raise AppTokenMintError("GITHUB_APP_PRIVATE_KEY is not configured")
        if not installation_id:
            raise AppTokenMintError("GitHub installation_id is required")

        jwt = _github_jwt(app_id=app_id, private_key_pem=private_key, now=int(time.time()))
        response = self._client().post(
            f"{self.settings.github_api_base_url.rstrip('/')}/app/installations/{quote(installation_id)}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"permissions": app_payload.get("permissions", {"contents": "write", "pull_requests": "write"})},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AppTokenMintError(f"GitHub App token exchange failed: HTTP {exc.response.status_code}") from exc
        body = response.json()
        token = str(body.get("token") or "")
        if not token:
            raise AppTokenMintError("GitHub App token response missing token")
        expires_at = _parse_expiry(body.get("expires_at")) or (self._now() + timedelta(minutes=50))
        return token, expires_at

    def _mint_gitlab(self, app_payload: dict[str, Any]) -> tuple[str, datetime]:
        installation_id = str(app_payload.get("installation_id") or "").strip()
        bootstrap_token = str(
            app_payload.get("bootstrap_token") or self.settings.gitlab_app_bootstrap_token
        ).strip()
        target_type = str(app_payload.get("target_type") or "project").strip().lower()
        scopes = app_payload.get("scopes") or ["api", "write_repository"]
        expires_at = (self._now() + timedelta(hours=1)).date().isoformat()
        if not installation_id:
            raise AppTokenMintError("GitLab installation_id is required")
        if target_type not in {"project", "group"}:
            raise AppTokenMintError("GitLab target_type must be project or group")
        if not bootstrap_token:
            raise AppTokenMintError("GITLAB_APP_BOOTSTRAP_TOKEN is not configured")

        base = self.settings.gitlab_api_base_url.rstrip("/")
        collection = "projects" if target_type == "project" else "groups"
        response = self._client().post(
            f"{base}/{collection}/{quote(installation_id, safe='')}/access_tokens",
            headers={"PRIVATE-TOKEN": bootstrap_token},
            json={
                "name": str(app_payload.get("name") or "haao-runner-pr-flow"),
                "scopes": scopes,
                "expires_at": expires_at,
                "access_level": int(app_payload.get("access_level") or 40),
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AppTokenMintError(f"GitLab App token exchange failed: HTTP {exc.response.status_code}") from exc
        body = response.json()
        token = str(body.get("token") or "")
        if not token:
            raise AppTokenMintError("GitLab token response missing token")
        return token, datetime.combine(
            datetime.fromisoformat(expires_at).date(),
            datetime.min.time(),
            tzinfo=UTC,
        ) + timedelta(hours=23, minutes=59)

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=15.0)
        return self._http_client


def _cache_key(provider: str, payload: dict[str, Any]) -> tuple[str, str]:
    installation_id = str(payload.get("installation_id") or "")
    target_type = str(payload.get("target_type") or "")
    return provider, f"{target_type}:{installation_id}"


def _github_jwt(*, app_id: str, private_key_pem: str, now: int) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": app_id}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parse_expiry(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
