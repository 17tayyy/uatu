from typing import Annotated

from fastapi import APIRouter, Path, status

from uatu.web.dependencies import CurrentUser, DbSession, SameOrigin
from uatu.web.reviews import service as review_service
from uatu.web.reviews.schemas import ReviewRead, ReviewWrite
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/detections", tags=["reviews"], dependencies=[SameOrigin])
DetectionId = Annotated[int, Path(ge=1)]


@router.put("/{detection_id}/review")
async def put_review(
    db: DbSession, user: CurrentUser, detection_id: DetectionId, data: ReviewWrite
) -> DataResponse[ReviewRead]:
    """Your verdict on a detection: a vessel with or without AIS, a reception gap, or not a vessel."""
    return DataResponse(data=await review_service.put_review(db, detection_id, data, user))


@router.delete("/{detection_id}/review", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(db: DbSession, _user: CurrentUser, detection_id: DetectionId) -> None:
    await review_service.delete_review(db, detection_id)
