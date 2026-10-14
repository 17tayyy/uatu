"""How well aisstream.io receives AIS across the area, from what Uatu has recorded.

The area is cut into cells of ~2 km. In a well-covered cell, a vessel crossing it at 12 kn is
heard about once a minute for the five minutes it takes: several reports. Where a receiver
only catches it now and then, it is heard once. Cells with fewer than 3 vessels say nothing.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox
from uatu.db.models import Position
from uatu.web.coverage.schemas import CellProperties, Reception
from uatu.web.schemas import Feature, FeatureCollection, Geometry

CELL_DEG = 0.02
MIN_VESSELS = 3
GOOD_REPORTS_PER_VESSEL = 3.0
PATCHY_REPORTS_PER_VESSEL = 1.5


def reception(reports_per_vessel: float) -> Reception:
    if reports_per_vessel >= GOOD_REPORTS_PER_VESSEL:
        return Reception.GOOD
    if reports_per_vessel >= PATCHY_REPORTS_PER_VESSEL:
        return Reception.PATCHY
    return Reception.POOR


async def coverage_cells(db: AsyncSession, bbox: BBox, days: float) -> FeatureCollection[CellProperties]:
    lat_min, lon_min, lat_max, lon_max = bbox
    row_ = func.floor(Position.lat / CELL_DEG).label("row")
    col_ = func.floor(Position.lon / CELL_DEG).label("col")
    vessels = func.count(distinct(Position.mmsi))
    rows = await db.execute(
        select(row_, col_, vessels.label("vessels"), func.count().label("reports"))
        .where(
            Position.received_at >= datetime.now(UTC) - timedelta(days=days),
            Position.lat.between(lat_min, lat_max),
            Position.lon.between(lon_min, lon_max),
        )
        .group_by(row_, col_)
        .having(vessels >= MIN_VESSELS)
        .order_by(row_, col_)
    )
    features = []
    for r in rows:
        south, west = r.row * CELL_DEG, r.col * CELL_DEG
        ring = [[west, south], [west + CELL_DEG, south], [west + CELL_DEG, south + CELL_DEG],
                [west, south + CELL_DEG], [west, south]]  # fmt: skip
        per_vessel = r.reports / r.vessels
        features.append(
            Feature(
                geometry=Geometry(type="Polygon", coordinates=[[[round(x, 4), round(y, 4)] for x, y in ring]]),
                properties=CellProperties(
                    vessels=r.vessels,
                    reports=r.reports,
                    reports_per_vessel=round(per_vessel, 1),
                    reception=reception(per_vessel),
                ),
            )
        )
    return FeatureCollection(features=features)
