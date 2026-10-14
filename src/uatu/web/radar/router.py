import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import Response

from uatu.web.dependencies import DbSession, Settings, ZoneScope
from uatu.web.radar import service as radar_service

router = APIRouter(prefix="/radar", tags=["radar"])


@router.get("/tiles/{z}/{x}/{y}.png", response_class=Response)
async def recent_radar_tile(
    z: Annotated[int, Path(ge=0, le=18)],
    x: Annotated[int, Path(ge=0)],
    y: Annotated[int, Path(ge=0)],
    db: DbSession,
    settings: Settings,
    scope: ZoneScope,
    days: Annotated[float, Query(gt=0, le=30)] = 7,
) -> Response:
    """The latest radar image of every place in the zone over the last `days`: newest pass on top. Empty (204)
    where no pass imaged it."""
    frames = await radar_service.recent_frames(db, settings.sar_data_dir, scope.keys, days, z, x, y)
    png = await asyncio.to_thread(radar_service.mosaic_tile, frames, z, x, y) if frames else None
    if png is None:
        return Response(status_code=204)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=600"})
