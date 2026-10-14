from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import FileResponse

from uatu.web.dependencies import DbSession, Settings
from uatu.web.passes import service as pass_service
from uatu.web.passes.schemas import PassDetail, PassSummary
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/passes", tags=["passes"])

PassId = Annotated[str, Path(pattern=pass_service.PASS_ID_PATTERN, max_length=10)]


@router.get("")
async def list_passes(
    db: DbSession, settings: Settings, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> DataResponse[list[PassSummary]]:
    """Satellite passes since AIS recording began, latest first."""
    return DataResponse(data=await pass_service.list_passes(db, settings.sar_data_dir, settings.sar_bbox, limit))


@router.get("/{pass_id}")
async def get_pass(pass_id: PassId, db: DbSession, settings: Settings) -> DataResponse[PassDetail]:
    return DataResponse(data=await pass_service.get_pass(db, settings.sar_data_dir, settings.sar_bbox, pass_id))


@router.get("/{pass_id}/images/{frame}", response_class=FileResponse)
async def get_pass_image(
    pass_id: PassId, frame: Annotated[int, Path(ge=0, le=9)], db: DbSession, settings: Settings
) -> FileResponse:
    """A frame's radar backscatter (VV), to lay on the map at the coordinates in the pass detail."""
    path = await pass_service.get_pass_image(db, settings.sar_data_dir, pass_id, frame)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
