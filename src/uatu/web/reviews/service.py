"""Verdicts on radar detections: what each turned out to be. The labels of a future classifier."""

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import DashboardUser, DetectionReview, SarDetection, Vessel
from uatu.web.errors import NotFoundError
from uatu.web.reviews.schemas import ReviewRead, ReviewWrite

_reviewed = Vessel.__table__.alias("reviewed_vessel")
# What a ReviewRead is made of, to select alongside a detection (with join_reviews).
REVIEW_COLUMNS = (
    DetectionReview.verdict.label("review_verdict"),
    DetectionReview.mmsi.label("review_mmsi"),
    _reviewed.c.name.label("review_vessel_name"),
    DetectionReview.note.label("review_note"),
    DashboardUser.username.label("review_by"),
    DetectionReview.reviewed_at.label("review_at"),
)


def join_reviews(query: Any) -> Any:
    """A select of detections, outer-joined to their verdicts for REVIEW_COLUMNS."""
    return (
        query.outerjoin(DetectionReview, DetectionReview.detection_id == SarDetection.id)
        .outerjoin(_reviewed, _reviewed.c.mmsi == DetectionReview.mmsi)
        .outerjoin(DashboardUser, DashboardUser.id == DetectionReview.reviewed_by)
    )


def review_of(row: Any) -> ReviewRead | None:
    if row.review_verdict is None:
        return None
    return ReviewRead(
        verdict=row.review_verdict,
        mmsi=row.review_mmsi,
        vessel_name=row.review_vessel_name,
        note=row.review_note,
        reviewed_by=row.review_by,
        reviewed_at=row.review_at,
    )


async def get_review(db: AsyncSession, detection_id: int) -> ReviewRead | None:
    query = join_reviews(select(*REVIEW_COLUMNS).select_from(SarDetection)).where(SarDetection.id == detection_id)
    row = (await db.execute(query)).first()
    return review_of(row) if row else None


async def put_review(db: AsyncSession, detection_id: int, data: ReviewWrite, user: DashboardUser) -> ReviewRead:
    """Give a detection its verdict, or change it."""
    detection = await db.get(SarDetection, detection_id)
    if detection is None:
        raise NotFoundError("Detection")
    values = {"verdict": data.verdict.value, "mmsi": data.mmsi, "note": data.note, "reviewed_by": user.id}
    await db.execute(
        pg_insert(DetectionReview)
        .values(scene_id=detection.scene_id, detection_id=detection.id, lat=detection.lat, lon=detection.lon, **values)
        .on_conflict_do_update(
            index_elements=[DetectionReview.detection_id], set_={**values, "reviewed_at": func.now()}
        )
    )
    await db.commit()
    review = await get_review(db, detection_id)
    assert review is not None
    return review


async def delete_review(db: AsyncSession, detection_id: int) -> None:
    removed = await db.execute(delete(DetectionReview).where(DetectionReview.detection_id == detection_id))
    if removed.rowcount == 0:
        raise NotFoundError("Review")
    await db.commit()
