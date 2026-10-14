from fastapi import APIRouter

from uatu.web.basemap import service as basemap_service
from uatu.web.basemap.schemas import InfrastructureProperties, LandProperties, MapArea
from uatu.web.dependencies import DbSession, LandOutlines, Settings
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/area")
async def area(settings: Settings) -> DataResponse[MapArea]:
    """The watched area: where AIS is recorded."""
    return DataResponse(data=basemap_service.area(settings.ais_bbox))


@router.get("/land")
async def land(outlines: LandOutlines) -> DataResponse[FeatureCollection[LandProperties]]:
    """Land outlines over the area (built once at startup from the SAR land mask)."""
    return DataResponse(data=outlines)


@router.get("/infrastructure")
async def infrastructure(
    db: DbSession, settings: Settings
) -> DataResponse[FeatureCollection[InfrastructureProperties]]:
    return DataResponse(data=await basemap_service.infrastructure(db, settings.ais_bbox))
