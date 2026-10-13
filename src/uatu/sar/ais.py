"""AIS ground truth for a SAR acquisition: where each vessel was when the radar looked at it."""

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.config import BBox
from uatu.db.models import Position, Vessel
from uatu.sar.annotation import AcquisitionGeometry

MAX_GAP = timedelta(minutes=10)  # never estimate a position further than this from a real report
_METRES_PER_DEGREE = 111_320.0


@dataclass(frozen=True, slots=True)
class Report:
    t: datetime
    lon: float
    lat: float
    sog: float | None
    cog: float | None


@dataclass(frozen=True, slots=True)
class AisVessel:
    mmsi: int
    name: str | None
    ship_type: int | None
    length_m: int | None
    lon: float  # where it was when the radar looked at that spot
    lat: float
    sog: float | None  # knots
    cog: float | None  # degrees
    seen_at: datetime
    report_gap_s: float  # time to the nearest real AIS report: how much of the position is estimated


async def vessels_at_acquisition(
    engine: AsyncEngine, geometry: AcquisitionGeometry, duration_s: float, bbox: BBox
) -> list[AisVessel]:
    """Every vessel with AIS reports close enough in time to place it at the moment of acquisition."""
    start = geometry.start - MAX_GAP
    end = geometry.start + timedelta(seconds=duration_s) + MAX_GAP
    tracks = await _tracks(engine, start, end, bbox)
    statics = await _statics(engine, list(tracks))
    middle = geometry.start + timedelta(seconds=duration_s / 2)

    found = []
    for mmsi, reports in tracks.items():
        # Place the vessel at mid-acquisition, then refine with the time the radar saw that spot.
        t, estimate = middle, None
        for _ in range(2):
            estimate = predict(reports, t)
            if estimate is None:
                break
            t = geometry.time_at(np.array([estimate[0]]), np.array([estimate[1]]))[0]
        if estimate is None:
            continue
        lon, lat, sog, cog, gap_s = estimate
        name, ship_type, length_m = statics.get(mmsi, (None, None, None))
        found.append(AisVessel(mmsi, name, ship_type, length_m, lon, lat, sog, cog, t, gap_s))
    return found


def predict(reports: list[Report], t: datetime) -> tuple[float, float, float | None, float | None, float] | None:
    """Position at time t from a vessel's time-ordered reports: interpolated between the reports
    around t, or dead-reckoned from the nearest one with its speed and course."""
    i = bisect_left([r.t for r in reports], t)
    before = reports[i - 1] if i > 0 else None
    after = reports[i] if i < len(reports) else None
    if before is not None and after is not None and after.t - before.t <= 2 * MAX_GAP:
        f = (t - before.t) / (after.t - before.t) if after.t > before.t else 0.0
        nearest = before if f < 0.5 else after
        gap = min(t - before.t, after.t - t).total_seconds()
        return (
            before.lon + f * (after.lon - before.lon),
            before.lat + f * (after.lat - before.lat),
            nearest.sog,
            nearest.cog,
            gap,
        )
    candidates = [r for r in (before, after) if r is not None]
    nearest = min(candidates, key=lambda r: abs((t - r.t).total_seconds()))
    dt = (t - nearest.t).total_seconds()
    if abs(dt) > MAX_GAP.total_seconds():
        return None
    lon, lat = nearest.lon, nearest.lat
    if nearest.sog is not None and nearest.cog is not None:
        distance = nearest.sog * 0.514444 * dt
        lat += distance * math.cos(math.radians(nearest.cog)) / _METRES_PER_DEGREE
        lon += distance * math.sin(math.radians(nearest.cog)) / (_METRES_PER_DEGREE * math.cos(math.radians(lat)))
    return lon, lat, nearest.sog, nearest.cog, abs(dt)


async def _tracks(engine: AsyncEngine, start: datetime, end: datetime, bbox: BBox) -> dict[int, list[Report]]:
    lat_min, lon_min, lat_max, lon_max = bbox
    stmt = (
        select(Position.mmsi, Position.received_at, Position.lon, Position.lat, Position.sog, Position.cog)
        .where(
            Position.received_at.between(start, end),
            Position.lon.between(lon_min, lon_max),
            Position.lat.between(lat_min, lat_max),
        )
        .order_by(Position.mmsi, Position.received_at)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    tracks: dict[int, list[Report]] = {}
    for mmsi, t, lon, lat, sog, cog in rows:
        tracks.setdefault(mmsi, []).append(Report(t, lon, lat, sog, cog))
    return tracks


async def _statics(engine: AsyncEngine, mmsis: list[int]) -> dict[int, tuple[str | None, int | None, int | None]]:
    if not mmsis:
        return {}
    stmt = select(Vessel.mmsi, Vessel.name, Vessel.ship_type, Vessel.length_m).where(Vessel.mmsi.in_(mmsis))
    async with engine.connect() as conn:
        return {mmsi: (name, ship_type, length) for mmsi, name, ship_type, length in await conn.execute(stmt)}
