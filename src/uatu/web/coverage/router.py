from typing import Annotated

from fastapi import APIRouter, Query

from uatu.web.coverage import service as coverage_service
from uatu.web.coverage.schemas import CellProperties
from uatu.web.dependencies import DbSession, ZoneScope
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/coverage", tags=["coverage"])


@router.get("")
async def coverage(
    db: DbSession, scope: ZoneScope, days: Annotated[float, Query(gt=0, le=30)] = 7
) -> DataResponse[FeatureCollection[CellProperties]]:
    """AIS reception quality by ~2 km cell over the last `days`."""
    return DataResponse(data=await coverage_service.coverage_cells(db, scope.bbox, days))
