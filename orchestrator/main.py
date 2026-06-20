from contextlib import asynccontextmanager

from fastapi import FastAPI

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


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "lmstudio_base_url": settings.lmstudio_base_url,
    }
