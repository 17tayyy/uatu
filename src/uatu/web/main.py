"""The Uatu dashboard: a read-only map of passes, detections, AIS events and live vessels.

It has no authentication: serve it on the local network only, never on the internet.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import async_sessionmaker

from uatu.config import bbox_polygon
from uatu.db import create_engine
from uatu.log import configure_logging
from uatu.sar.raster import Grid
from uatu.web.basemap.router import router as basemap_router
from uatu.web.basemap.service import land_outlines
from uatu.web.config import WebSettings, get_settings
from uatu.web.coverage.router import router as coverage_router
from uatu.web.errors import COMMON_ERROR_RESPONSES, register_exception_handlers
from uatu.web.events.router import router as events_router
from uatu.web.health.router import router as health_router
from uatu.web.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from uatu.web.passes.router import router as passes_router
from uatu.web.schemas import FeatureCollection
from uatu.web.status.router import router as status_router
from uatu.web.vessels.router import router as vessels_router

log = structlog.get_logger(component="web")

STATIC_DIR = Path(__file__).parent / "static"
SAR_RESOLUTION_M = 10.0  # the grid the worker builds the land mask on


def load_land(settings: WebSettings) -> FeatureCollection:
    """Land outlines from the worker's land mask cache; none (sea everywhere) until the worker has built it."""
    grid = Grid.covering(bbox_polygon(settings.sar_bbox), SAR_RESOLUTION_M)
    path = settings.sar_data_dir / "cache" / f"land_{grid.key}.tif"
    if not path.exists():
        log.warning("web.no_land_mask", path=str(path))
        return FeatureCollection(features=[])
    return land_outlines(grid, path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: WebSettings = app.state.settings
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        statement_timeout_ms=settings.db_statement_timeout_ms,
    )
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app.state.land = await asyncio.to_thread(load_land, settings)
    log.info("web.started", land_polygons=len(app.state.land.features))
    try:
        yield
    finally:
        await engine.dispose()


def _operation_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}" if route.tags else route.name


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Uatu",
        summary="Dark vessel detection in the Strait of Gibraltar",
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
        responses=COMMON_ERROR_RESPONSES,
    )
    app.state.settings = settings
    register_exception_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)  # outermost: its request id covers everything

    api = APIRouter(prefix="/api/v1")
    for router in (status_router, passes_router, events_router, vessels_router, coverage_router, basemap_router):
        api.include_router(router)
    app.include_router(api)
    app.include_router(health_router)
    app.frontend("/", directory=STATIC_DIR, fallback=None)
    return app


app = create_app()
