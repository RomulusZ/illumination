"""FastAPI runtime assembly without importing the upper UI layer."""

from fastapi import FastAPI

from illumination.config.settings import Settings, get_settings
from illumination.runtime.composition import build_runtime


def create_runtime_app(settings: Settings | None = None, *, stub: bool = False) -> FastAPI:
    """Create the application state through the existing composition root."""
    resolved = settings or get_settings()
    app = FastAPI(title="Illumination", version="0.1.0")
    app.state.settings = resolved
    app.state.runtime = build_runtime(resolved, stub=stub)
    return app
