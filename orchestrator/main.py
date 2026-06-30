from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api import _sqlite_path, router
from orchestrator.authz import (
    AuthenticationError,
    AuthorizationError,
    auth_enabled,
    classify_action,
    require_action,
    resolve_auth_context,
)
from orchestrator.auto_worker import auto_worker
from orchestrator.config import get_settings
from orchestrator.db.sqlite import AuditRepository, IdentityRepository, RunnerRepository, SettingsRepository, connect
from orchestrator.sso import OIDCVerificationError, SESSION_COOKIE_NAME, decode_session_token
from orchestrator.role_routing import role_routing_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    connection = connect(_sqlite_path(settings.database_url))
    try:
        settings_repository = SettingsRepository(connection)
        role_routing_store.bind_settings_repository(settings_repository)
        yield
    finally:
        await auto_worker.stop()
        connection.close()


app = FastAPI(
    title="HAAO Orchestrator",
    version="0.1.0",
    description="Hybrid AI-Agile Orchestrator MVP API",
    lifespan=lifespan,
)

# Every route is declared once on `router` with a bare path and served at both the
# bare path and under `/api` (single source of truth, consistent public surface).
app.include_router(router)
app.include_router(router, prefix="/api")


@app.middleware("http")
async def api_token_auth(request: Request, call_next):
    settings = get_settings()
    token = settings.haao_api_token.strip()
    path = request.url.path
    # Gate every route except the unauthenticated health check. Endpoints are
    # dual-registered at both `/api/...` and bare `/...`, so gating only the
    # `/api/` prefix left the bare paths (e.g. /config/integrations, /chat/messages)
    # open — an auth bypass. Token-set => everything but /health requires it.
    if path == "/health":
        return await call_next(request)
    if _is_public_auth_endpoint(path):
        return await call_next(request)

    connection = connect(_sqlite_path(settings.database_url))
    try:
        settings_repository = SettingsRepository(connection)
        enforcement_enabled = auth_enabled(settings_repository, api_token=token)
        if _is_runner_endpoint(path):
            runner_token = _runner_token_from_request(request)
            if not runner_token or RunnerRepository(connection).authenticate(runner_token) is None:
                return JSONResponse({"detail": "Invalid or missing runner token"}, status_code=401)
            return await call_next(request)

        header_user_id = request.headers.get("x-haao-user-id")
        header_workspace_id = request.headers.get("x-haao-workspace-id")
        session_token = _session_token_from_request(request)
        has_api_token = False
        if token:
            expected = f"Bearer {token}"
            has_api_token = request.headers.get("authorization") == expected
            if not has_api_token and not session_token:
                return _auth_error_response(
                    "API token required",
                    status_code=401,
                    reason="api_token_required",
                    www_authenticate='Bearer realm="haao"',
                )

        if session_token and (not has_api_token or not header_user_id):
            try:
                session = decode_session_token(session_token)
            except OIDCVerificationError as exc:
                return _auth_error_response(
                    "Login required",
                    status_code=401,
                    reason="login_required",
                    www_authenticate='Bearer realm="haao"',
                )
            header_user_id = session.user_id
            header_workspace_id = session.workspace_id

        identity = IdentityRepository(connection)
        try:
            auth_context = resolve_auth_context(
                identity,
                user_id=header_user_id,
                workspace_id=header_workspace_id,
                auth_enabled=enforcement_enabled,
                api_token_authenticated=has_api_token,
            )
            action = classify_action(request.method, path)
            require_action(auth_context, action)
        except AuthenticationError as exc:
            return _auth_error_response(
                str(exc),
                status_code=401,
                reason=exc.reason,
                www_authenticate='Bearer realm="haao"',
            )
        except AuthorizationError as exc:
            return _auth_error_response(str(exc), status_code=403, reason=exc.reason)

        request.state.auth_context = auth_context
        response = await call_next(request)
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 400:
            AuditRepository(connection).append(
                actor_id=auth_context.actor_id,
                workspace_id=auth_context.workspace_id,
                action=f"{request.method.upper()} {path}",
                target=str(request.url.path),
                payload={"query": str(request.url.query or "")},
                ip=request.client.host if request.client else None,
            )
        return response
    finally:
        connection.close()


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "lmstudio_base_url": settings.lmstudio_base_url,
    }


def _is_runner_endpoint(path: str) -> bool:
    normalized = path[4:] if path.startswith("/api/") else path
    return (
        normalized in {"/runner/heartbeat", "/runner/lease", "/runner/events", "/runner/complete"}
        or (
            normalized.startswith("/runner/jobs/")
            and (normalized.endswith("/complete") or normalized.endswith("/release"))
        )
    )


def _auth_error_response(
    detail: str,
    *,
    status_code: int,
    reason: str,
    www_authenticate: str | None = None,
) -> JSONResponse:
    headers = {"WWW-Authenticate": www_authenticate} if www_authenticate else None
    return JSONResponse(
        {"detail": detail, "reason": reason},
        status_code=status_code,
        headers=headers,
    )


def _is_public_auth_endpoint(path: str) -> bool:
    normalized = path[4:] if path.startswith("/api/") else path
    return normalized in {"/auth/oidc/login", "/auth/oidc/callback"}


def _runner_token_from_request(request: Request) -> str:
    header = request.headers.get("x-haao-runner-token", "")
    if header:
        return header
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return ""


def _session_token_from_request(request: Request) -> str:
    header = request.headers.get("x-haao-session", "")
    if header:
        return header
    cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie:
        return cookie
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()
        if bearer.startswith("hses."):
            return bearer
    return ""
