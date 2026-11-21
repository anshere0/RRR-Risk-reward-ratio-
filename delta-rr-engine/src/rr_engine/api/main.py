"""FastAPI application factory and lifecycle management."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rr_engine import __version__
from rr_engine.api.routes import router
from rr_engine.config import Settings, get_settings
from rr_engine.engine.rr_engine import RREngine
from rr_engine.feed.manager import FeedManager


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start feed manager on startup; stop on shutdown."""
    settings = getattr(app.state, "settings", None) or get_settings()
    feed_manager = getattr(app.state, "feed_manager", None) or FeedManager(settings=settings)
    engine = getattr(app.state, "engine", None) or RREngine(feed_manager=feed_manager, settings=settings)

    app.state.settings = settings
    app.state.feed_manager = feed_manager
    app.state.engine = engine

    # Start feed in background if enabled
    auto_start = getattr(app.state, "auto_start_feed", True)
    if auto_start:
        await feed_manager.start()

    yield

    if auto_start:
        await feed_manager.stop()


def create_app(
    settings: Settings | None = None,
    feed_manager: FeedManager | None = None,
    engine: RREngine | None = None,
    auto_start_feed: bool = True,
) -> FastAPI:
    """Create configured FastAPI application."""
    app = FastAPI(
        title="Delta Exchange BTC Options Risk-Reward Engine",
        version=__version__,
        description="Local, read-only decision-support tool for BTC options on Delta Exchange.",
        lifespan=lifespan,
    )

    # Local-only CORS policy (F9: removed delta.exchange origins and allow_credentials)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    # F9: Trusted Host lock down
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", "test"],
    )

    app.state.settings = settings or get_settings()
    app.state.feed_manager = feed_manager or FeedManager(settings=app.state.settings)
    app.state.engine = engine or RREngine(feed_manager=app.state.feed_manager, settings=app.state.settings)
    app.state.auto_start_feed = auto_start_feed

    app.include_router(router)

    # Mount static HUD files if directory exists
    hud_dir = Path(__file__).parent.parent / "hud"
    if hud_dir.exists():
        app.mount("/hud", StaticFiles(directory=str(hud_dir), html=True), name="hud")

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/hud/")

    return app


app = create_app()
