from typing import Annotated

from fastapi import APIRouter, Query

from uatu.web.dependencies import DbSession
from uatu.web.events import service as event_service
from uatu.web.events.schemas import EventProperties
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(
    db: DbSession,
    hours: Annotated[float, Query(gt=0, le=24 * 30)] = 48,
    min_severity: Annotated[int, Query(ge=1, le=3)] = 2,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> DataResponse[FeatureCollection[EventProperties]]:
    """AIS events going on during the last `hours`, latest first."""
    return DataResponse(data=await event_service.list_events(db, hours, min_severity, limit))
