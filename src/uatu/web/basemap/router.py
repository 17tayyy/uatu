from fastapi import APIRouter

from uatu.web.basemap import service as basemap_service
from uatu.web.basemap.schemas import InfrastructureProperties, LandProperties, SatelliteLayer
from uatu.web.dependencies import DbSession, LandOutlines, Settings, ZoneScope
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/land")
async def land(outlines: LandOutlines) -> DataResponse[FeatureCollection[LandProperties]]:
    """Land outlines over the whole coast (built once at startup from the zones' land masks)."""
    return DataResponse(data=outlines)


@router.get("/satellite")
async def satellite(settings: Settings) -> DataResponse[SatelliteLayer]:
    """Where the browser gets the optical imagery shown on land, under the radar."""
    key = settings.maptiler_key.get_secret_value() if settings.maptiler_key else None
    return DataResponse(data=basemap_service.satellite(key))


@router.get("/infrastructure")
async def infrastructure(db: DbSession, scope: ZoneScope) -> DataResponse[FeatureCollection[InfrastructureProperties]]:
    return DataResponse(data=await basemap_service.infrastructure(db, scope.bbox))
