from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Response

from uatu.web.dependencies import DbSession, ZoneScope
from uatu.web.history import service as history_service
from uatu.web.history.schemas import DensityProperties, HistoryChunk, HistoryRange
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/history", tags=["history"])


@router.get("")
async def history_range(db: DbSession) -> DataResponse[HistoryRange]:
    """The span of the AIS record."""
    return DataResponse(data=await history_service.history_range(db))


@router.get("/tracks")
async def tracks(
    db: DbSession,
    scope: ZoneScope,
    response: Response,
    start: Annotated[datetime, Query(description="ISO time, e.g. 2026-10-13T21:00Z")],
    hours: Annotated[float, Query(gt=0, le=3)] = 1,
) -> DataResponse[HistoryChunk]:
    """Every AIS report in the area from `start` for `hours`, by vessel, to play back."""
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    chunk = await history_service.tracks(db, scope.bbox, start, hours)
    # Past hours don't change: let the browser keep them.
    settled = chunk.end < datetime.now(UTC) - timedelta(minutes=5)
    response.headers["Cache-Control"] = "public, max-age=86400" if settled else "public, max-age=60"
    return DataResponse(data=chunk)


@router.get("/density")
async def density(
    db: DbSession, scope: ZoneScope, days: Annotated[float, Query(gt=0, le=30)] = 7
) -> DataResponse[FeatureCollection[DensityProperties]]:
    """Where traffic gathers: vessel-hours per ~2 km cell over the last `days`."""
    return DataResponse(data=await history_service.density(db, scope.bbox, days))
