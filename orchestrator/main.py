from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.api import _sqlite_path, router
from orchestrator.auto_worker import auto_worker
from orchestrator.config import get_settings
from orchestrator.db.sqlite import SettingsRepository, connect
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
    # Gate every route except the unauthenticated health check. Endpoints are
    # dual-registered at both `/api/...` and bare `/...`, so gating only the
    # `/api/` prefix left the bare paths (e.g. /config/integrations, /chat/messages)
    # open — an auth bypass. Token-set => everything but /health requires it.
    if token and request.url.path != "/health":
        expected = f"Bearer {token}"
        if request.headers.get("authorization") != expected:
            return JSONResponse({"detail": "Invalid or missing API token"}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "lmstudio_base_url": settings.lmstudio_base_url,
    }
