from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import FileResponse

from uatu.web.dependencies import DbSession, HttpClient, Settings
from uatu.web.optical import service as optical_service
from uatu.web.optical.schemas import OpticalImage
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/detections", tags=["optical"])

DetectionId = Annotated[int, Path(ge=1)]


@router.get("/{detection_id}/optical")
async def optical(
    detection_id: DetectionId, db: DbSession, http: HttpClient, settings: Settings
) -> DataResponse[OpticalImage | None]:
    """Sentinel-2 true colour around a radar detection, the clearest within 3 days of it (none: null).
    The first request looks it up (a few seconds); then it is kept."""
    cache = settings.web_cache_dir / "optical"
    return DataResponse(data=await optical_service.optical(db, http, cache, detection_id))


@router.get("/{detection_id}/optical.png", response_class=FileResponse)
async def optical_png(detection_id: DetectionId, settings: Settings) -> FileResponse:
    path = optical_service.optical_png(settings.web_cache_dir / "optical", detection_id)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
