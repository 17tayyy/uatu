"""The AIS record played back: every vessel's reports, an hour at a time, and where traffic gathers.

Positions are sent as recorded; the dashboard interpolates between reports and draws trails.
"""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox
from uatu.db.models import Position, RecordedZone, SanctionedVessel, Vessel, VesselRisk
from uatu.web.geo import inside
from uatu.web.history.schemas import DensityProperties, HistoryChunk, HistoryRange, HistoryTrack
from uatu.web.schemas import Feature, FeatureCollection, point
from uatu.web.vessels.labels import flag, ship_type

CELL_DEG = 0.02  # ~2 km, as the AIS coverage cells


async def history_range(db: AsyncSession) -> HistoryRange:
    now = datetime.now(UTC)
    since = await db.scalar(select(func.min(RecordedZone.since)))
    return HistoryRange(start=since or now, end=now)


async def tracks(db: AsyncSession, bbox: BBox, start: datetime, hours: float) -> HistoryChunk:
    """Every report in the area from `start` for `hours`, by vessel."""
    end = start + timedelta(hours=hours)
    rows = await db.execute(
        select(Position.mmsi, Position.received_at, Position.lon, Position.lat, Position.sog, Position.cog)
        .where(Position.received_at >= start, Position.received_at < end, inside(Position.lat, Position.lon, bbox))
        .order_by(Position.mmsi, Position.received_at)
    )
    reports: dict[int, list[Any]] = defaultdict(list)
    for r in rows:
        reports[r.mmsi].append(r)
    vessels = {}
    if reports:
        meta = await db.execute(
            select(
                Vessel.mmsi, Vessel.name, Vessel.ship_type, Vessel.length_m, SanctionedVessel.programs, VesselRisk.level
            )
            .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
            .outerjoin(VesselRisk, VesselRisk.imo == Vessel.imo)
            .where(Vessel.mmsi.in_(list(reports)))
        )
        vessels = {m.mmsi: m for m in meta}
    result = []
    for mmsi, rs in reports.items():
        v = vessels.get(mmsi)
        result.append(
            HistoryTrack(
                mmsi=mmsi,
                name=v.name if v else None,
                ship_type=ship_type(v.ship_type) if v else None,
                length_m=v.length_m if v else None,
                flag=flag(mmsi),
                sanctioned=bool(v and v.programs is not None),
                risk=v.level if v else None,
                t=[int(r.received_at.timestamp()) for r in rs],
                lon=[round(r.lon, 5) for r in rs],
                lat=[round(r.lat, 5) for r in rs],
                sog=[None if r.sog is None else round(r.sog, 1) for r in rs],
                cog=[None if r.cog is None else round(r.cog) for r in rs],
            )
        )
    return HistoryChunk(start=start, end=end, tracks=result)


async def density(db: AsyncSession, bbox: BBox, days: float) -> FeatureCollection[DensityProperties]:
    """Vessel-hours per ~2 km cell over the last `days`: a vessel counts once per hour it spent in a cell,
    however often it reported, so a chatty transponder weighs no more than a quiet one."""
    row_ = func.floor(Position.lat / CELL_DEG).label("row")
    col_ = func.floor(Position.lon / CELL_DEG).label("col")
    vessel_hours = func.count(distinct(tuple_(Position.mmsi, func.date_trunc("hour", Position.received_at))))
    rows = (
        await db.execute(
            select(row_, col_, vessel_hours.label("vessel_hours"))
            .where(
                Position.received_at >= datetime.now(UTC) - timedelta(days=days),
                inside(Position.lat, Position.lon, bbox),
            )
            .group_by(row_, col_)
            .order_by(row_, col_)
        )
    ).all()
    busiest = math.log1p(max((r.vessel_hours for r in rows), default=1))
    return FeatureCollection(
        features=[
            Feature(
                geometry=point((r.col + 0.5) * CELL_DEG, (r.row + 0.5) * CELL_DEG),
                properties=DensityProperties(
                    vessel_hours=r.vessel_hours, weight=round(math.log1p(r.vessel_hours) / busiest, 3)
                ),
            )
            for r in rows
        ]
    )
