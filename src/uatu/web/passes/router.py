from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from uatu.web.dependencies import DbSession, Settings
from uatu.web.passes import service as pass_service
from uatu.web.passes.schemas import PassDetail, PassSummary
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/passes", tags=["passes"])


@router.get("")
async def list_passes(
    db: DbSession, settings: Settings, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> DataResponse[list[PassSummary]]:
    """Satellite passes since AIS recording began, latest first."""
    return DataResponse(data=await pass_service.list_passes(db, settings.sar_data_dir, limit))


@router.get("/{pass_id}")
async def get_pass(pass_id: UUID, db: DbSession, settings: Settings) -> DataResponse[PassDetail]:
    return DataResponse(data=await pass_service.get_pass(db, settings.sar_data_dir, pass_id))


@router.get("/{pass_id}/image", response_class=FileResponse)
async def get_pass_image(pass_id: UUID, db: DbSession, settings: Settings) -> FileResponse:
    """The pass's radar backscatter (VV), to lay on the map at the coordinates in its detail."""
    path = await pass_service.get_pass_image(db, settings.sar_data_dir, pass_id)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
