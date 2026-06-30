from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from orchestrator.db.sqlite import IdentityRepository, MembershipRole, SettingsRepository
from orchestrator.sso import OIDCConfigRepository

Role = Literal["owner", "admin", "member", "viewer"]
Action = Literal["read", "mutate", "admin"]

ROLE_PERMISSIONS: dict[Role, set[Action]] = {
    "owner": {"read", "mutate", "admin"},
    "admin": {"read", "mutate", "admin"},
    "member": {"read", "mutate"},
    "viewer": {"read"},
}

ADMIN_PATH_MARKERS = (
    "/config/cloud-models",
    "/config/integrations",
    "/projects/",
    "/settings",
    "/runner/register",
    "/runner/revoke",
    "/runner/jobs",
    "/members",
    "/retention",
    "/auth/oidc",
)

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REQUIRE_AUTH_SETTINGS_KEY = "require_auth"


@dataclass(frozen=True)
class AuthContext:
    actor_id: str
    workspace_id: str
    role: Role
    implicit_owner: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "role": self.role,
            "implicit_owner": self.implicit_owner,
        }


class AuthorizationError(PermissionError):
    """Raised when an actor is authenticated but not allowed to perform an action."""

    reason = "forbidden"


class AuthenticationError(PermissionError):
    """Raised when identity is enabled and the actor cannot be resolved."""

    def __init__(self, message: str = "Authentication required", *, reason: str = "login_required") -> None:
        super().__init__(message)
        self.reason = reason


def auth_enabled(
    settings_repository: SettingsRepository,
    *,
    api_token: str | None = None,
) -> bool:
    require_auth = settings_repository.get_json(REQUIRE_AUTH_SETTINGS_KEY, default=False)
    if require_auth is True:
        return True
    if api_token and api_token.strip():
        return True
    oidc_config = OIDCConfigRepository(settings_repository).get()
    return bool(oidc_config and oidc_config.configured)


def classify_action(method: str, path: str) -> Action:
    normalized = _normalized_path(path)
    if method.upper() not in MUTATION_METHODS:
        return "read"
    if any(marker in normalized for marker in ADMIN_PATH_MARKERS):
        return "admin"
    return "mutate"


def resolve_auth_context(
    identity: IdentityRepository,
    *,
    user_id: str | None,
    workspace_id: str | None,
    auth_enabled: bool = False,
    api_token_authenticated: bool = False,
) -> AuthContext:
    if not auth_enabled or (api_token_authenticated and not user_id):
        return AuthContext(
            actor_id=user_id or "implicit-owner",
            workspace_id=workspace_id or "default",
            role="owner",
            implicit_owner=True,
        )

    if not user_id:
        raise AuthenticationError("Missing authenticated user")
    workspace = workspace_id or "default"
    membership = identity.get_membership(user_id=user_id, workspace_id=workspace)
    if membership is None:
        raise AuthorizationError("User is not a member of this workspace")
    return AuthContext(
        actor_id=user_id,
        workspace_id=workspace,
        role=membership.role,
        implicit_owner=False,
    )


def require_action(context: AuthContext, action: Action) -> None:
    if action not in ROLE_PERMISSIONS[context.role]:
        raise AuthorizationError(f"Role {context.role} cannot perform {action}")


def _normalized_path(path: str) -> str:
    if path.startswith("/api/"):
        return path[4:]
    return path
