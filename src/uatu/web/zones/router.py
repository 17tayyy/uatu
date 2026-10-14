from fastapi import APIRouter

from uatu.web.dependencies import DbSession, Settings
from uatu.web.schemas import DataResponse
from uatu.web.zones import service as zone_service
from uatu.web.zones.schemas import Zones

router = APIRouter(prefix="/zones", tags=["zones"])


@router.get("")
async def zones(db: DbSession, settings: Settings) -> DataResponse[Zones]:
    """The zones analysed, and the box around them all. Other endpoints take one's key as `zone`."""
    return DataResponse(data=await zone_service.zones(db, settings.zones))
