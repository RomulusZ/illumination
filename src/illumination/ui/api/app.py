"""Complete HTTP application factory for TestClient and ASGI servers."""

from fastapi import FastAPI

from illumination.config.settings import Settings
from illumination.runtime.app import create_runtime_app
from illumination.types.auth import HealthResponse
from illumination.ui.api.auth import router as auth_router
from illumination.ui.api.errors import install_error_handlers


def create_app(settings: Settings | None = None, *, stub: bool = False) -> FastAPI:
    app = create_runtime_app(settings, stub=stub)
    install_error_handlers(app)
    app.include_router(auth_router)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    return app
