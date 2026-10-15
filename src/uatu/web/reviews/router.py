from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from uatu.web.dependencies import CurrentUser, DbSession, SameOrigin, ZoneScope
from uatu.web.passes import service as pass_service
from uatu.web.passes.schemas import PendingDetection
from uatu.web.reviews import service as review_service
from uatu.web.reviews.schemas import ReviewRead, ReviewWrite
from uatu.web.schemas import DataResponse, FeatureCollection

router = APIRouter(prefix="/detections", tags=["reviews"], dependencies=[SameOrigin])
DetectionId = Annotated[int, Path(ge=1)]


@router.get("/pending")
async def pending_detections(
    db: DbSession,
    scope: ZoneScope,
    days: Annotated[float, Query(gt=0, le=90)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> DataResponse[FeatureCollection[PendingDetection]]:
    """What is left to review across passes: candidates and reception gaps without a verdict, latest first."""
    since = datetime.now(UTC) - timedelta(days=days)
    return DataResponse(data=await pass_service.pending_detections(db, scope.keys, since, limit))


@router.put("/{detection_id}/review")
async def put_review(
    db: DbSession, user: CurrentUser, detection_id: DetectionId, data: ReviewWrite
) -> DataResponse[ReviewRead]:
    """Your verdict on a detection: a vessel with or without AIS, a reception gap, or not a vessel."""
    return DataResponse(data=await review_service.put_review(db, detection_id, data, user))


@router.delete("/{detection_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(db: DbSession, _user: CurrentUser, detection_id: DetectionId) -> None:
    await review_service.delete_review(db, detection_id)
