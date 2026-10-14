from fastapi import APIRouter

from uatu.web.basemap import service as basemap_service
from uatu.web.basemap.schemas import InfrastructureProperties, LandProperties
from uatu.web.dependencies import DbSession, LandOutlines, ZoneScope
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/land")
async def land(outlines: LandOutlines) -> DataResponse[FeatureCollection[LandProperties]]:
    """Land outlines over the whole coast (built once at startup from the zones' land masks)."""
    return DataResponse(data=outlines)


@router.get("/infrastructure")
async def infrastructure(db: DbSession, scope: ZoneScope) -> DataResponse[FeatureCollection[InfrastructureProperties]]:
    return DataResponse(data=await basemap_service.infrastructure(db, scope.bbox))
