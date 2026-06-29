from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api import _sqlite_path, router
from orchestrator.authz import (
    AuthenticationError,
    AuthorizationError,
    classify_action,
    require_action,
    resolve_auth_context,
)
from orchestrator.auto_worker import auto_worker
from orchestrator.config import get_settings
from orchestrator.db.sqlite import AuditRepository, IdentityRepository, RunnerRepository, SettingsRepository, connect
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

app.include_router(router)


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

    connection = connect(_sqlite_path(settings.database_url))
    try:
        if _is_runner_endpoint(path):
            runner_token = _runner_token_from_request(request)
            if not runner_token or RunnerRepository(connection).authenticate(runner_token) is None:
                return JSONResponse({"detail": "Invalid or missing runner token"}, status_code=401)
            return await call_next(request)

        if token:
            expected = f"Bearer {token}"
            if request.headers.get("authorization") != expected:
                return JSONResponse({"detail": "Invalid or missing API token"}, status_code=401)

        identity = IdentityRepository(connection)
        try:
            auth_context = resolve_auth_context(
                identity,
                user_id=request.headers.get("x-haao-user-id"),
                workspace_id=request.headers.get("x-haao-workspace-id"),
            )
            action = classify_action(request.method, path)
            require_action(auth_context, action)
        except AuthenticationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        except AuthorizationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)

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
        normalized in {"/runner/heartbeat", "/runner/lease", "/runner/events"}
        or (normalized.startswith("/runner/jobs/") and normalized.endswith("/complete"))
    )


def _runner_token_from_request(request: Request) -> str:
    header = request.headers.get("x-haao-runner-token", "")
    if header:
        return header
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return ""
