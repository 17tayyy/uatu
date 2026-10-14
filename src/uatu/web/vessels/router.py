from typing import Annotated

from fastapi import APIRouter, Path, Query

from uatu.web.dependencies import DbSession, Settings
from uatu.web.schemas import DataResponse, FeatureCollection
from uatu.web.vessels import service as vessel_service
from uatu.web.vessels.schemas import LiveVesselProperties, VesselDetail, VesselSummary

router = APIRouter(prefix="/vessels", tags=["vessels"])


@router.get("/live")
async def live_vessels(
    db: DbSession,
    settings: Settings,
    minutes: Annotated[float, Query(gt=0, le=24 * 60)] = 60,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
) -> DataResponse[FeatureCollection[LiveVesselProperties]]:
    """Last position of every vessel heard in the area in the last `minutes`."""
    return DataResponse(data=await vessel_service.live_vessels(db, settings.ais_bbox, minutes, limit))


@router.get("")
async def search_vessels(
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=40)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> DataResponse[list[VesselSummary]]:
    """Vessels by name (contains) or MMSI (starts with)."""
    return DataResponse(data=await vessel_service.search_vessels(db, q, limit))


@router.get("/{mmsi}")
async def get_vessel(
    db: DbSession,
    mmsi: Annotated[int, Path(ge=1, le=999_999_999)],
    track_hours: Annotated[float, Query(gt=0, le=24 * 7)] = 24,
) -> DataResponse[VesselDetail]:
    return DataResponse(data=await vessel_service.get_vessel(db, mmsi, track_hours))
