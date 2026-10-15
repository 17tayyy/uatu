"""AIS ground truth for a SAR acquisition: where each vessel was when the radar looked at it."""

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

import numpy as np
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.config import BBox
from uatu.db.models import Position, Vessel
from uatu.sar.annotation import AcquisitionGeometry

# Never estimate a position further than this from a real report. aisstream.io hears many vessels only
# every 10-30 minutes (anchored ones behind Gibraltar, about once an hour): at 10 minutes, 12 of the 48
# vessels the radar saw on 14 October went unpaired. The time machine draws a vessel for as long, so the
# map and the pairing agree on who was there.
MAX_GAP = timedelta(minutes=30)
SILENT_WINDOW = timedelta(hours=3)  # how long a vessel may have been silent to be a dark suspect
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
    # Fastest it may have gone since then: across an interpolation, the higher of the two reports'
    # speeds (a ferry keeps 29 kn until it slows to enter port; the nearest report may say 7).
    max_sog: float | None = None


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
        name, ship_type, length_m = statics.get(mmsi, (None, None, None))
        e = estimate
        found.append(AisVessel(mmsi, name, ship_type, length_m, e.lon, e.lat, e.sog, e.cog, t, e.gap_s, e.max_sog))
    return found


async def silent_vessels(
    engine: AsyncEngine, geometry: AcquisitionGeometry, duration_s: float, bbox: BBox, window: timedelta = SILENT_WINDOW
) -> list[AisVessel]:
    """Vessels heard within `window` of the acquisition but silent around it, placed where they
    would be: interpolated across the silence, or dead-reckoned from the last report.

    A ship the radar sees but AIS does not may be one of these, having switched its transponder
    off (or out of reception). report_gap_s says how long it had been silent.
    """
    start = geometry.start - window
    end = geometry.start + timedelta(seconds=duration_s) + window
    tracks = await _tracks(engine, start, end, bbox)
    middle = geometry.start + timedelta(seconds=duration_s / 2)
    silent = {mmsi: reports for mmsi, reports in tracks.items() if predict(reports, middle) is None}
    statics = await _statics(engine, list(silent))
    found = []
    for mmsi, reports in silent.items():
        t, estimate = middle, None
        for _ in range(2):
            estimate = predict(reports, t, max_gap=window)
            if estimate is None:
                break
            t = geometry.time_at(np.array([estimate[0]]), np.array([estimate[1]]))[0]
        if estimate is None:
            continue
        name, ship_type, length_m = statics.get(mmsi, (None, None, None))
        e = estimate
        found.append(AisVessel(mmsi, name, ship_type, length_m, e.lon, e.lat, e.sog, e.cog, t, e.gap_s, e.max_sog))
    return found


class Estimate(NamedTuple):
    lon: float
    lat: float
    sog: float | None  # of the nearest report
    cog: float | None
    gap_s: float  # to the nearest report
    max_sog: float | None  # fastest of the reports the estimate rests on


def predict(reports: list[Report], t: datetime, max_gap: timedelta = MAX_GAP) -> Estimate | None:
    """Position at time t from a vessel's time-ordered reports: interpolated between the reports
    around t, or dead-reckoned from the nearest one with its speed and course; None if no report
    is within max_gap of t."""
    i = bisect_left([r.t for r in reports], t)
    before = reports[i - 1] if i > 0 else None
    after = reports[i] if i < len(reports) else None
    if before is not None and after is not None and after.t - before.t <= 2 * max_gap:
        f = (t - before.t) / (after.t - before.t) if after.t > before.t else 0.0
        nearest = before if f < 0.5 else after
        gap = min(t - before.t, after.t - t).total_seconds()
        speeds = [r.sog for r in (before, after) if r.sog is not None]
        return Estimate(
            before.lon + f * (after.lon - before.lon),
            before.lat + f * (after.lat - before.lat),
            nearest.sog,
            nearest.cog,
            gap,
            max(speeds) if speeds else None,
        )
    candidates = [r for r in (before, after) if r is not None]
    nearest = min(candidates, key=lambda r: abs((t - r.t).total_seconds()))
    dt = (t - nearest.t).total_seconds()
    if abs(dt) > max_gap.total_seconds():
        return None
    lon, lat = nearest.lon, nearest.lat
    if nearest.sog is not None and nearest.cog is not None:
        distance = nearest.sog * 0.514444 * dt
        lat += distance * math.cos(math.radians(nearest.cog)) / _METRES_PER_DEGREE
        lon += distance * math.sin(math.radians(nearest.cog)) / (_METRES_PER_DEGREE * math.cos(math.radians(lat)))
    return Estimate(lon, lat, nearest.sog, nearest.cog, abs(dt), nearest.sog)


@dataclass(frozen=True, slots=True)
class Coverage:
    """AIS received around a place: how many vessels, and how many of their reports."""

    vessels: int
    reports: int

    @property
    def reports_per_vessel(self) -> float:
        return self.reports / self.vessels if self.vessels else 0.0


async def coverage_around(
    engine: AsyncEngine, points: list[tuple[float, float]], at: datetime, radius_m: float, window: timedelta
) -> list[Coverage]:
    """AIS received within radius_m of each (lon, lat) point, within ±window of `at`.

    A proxy for whether aisstream.io covers the place: vessels go by every shipping lane, and
    where coverage is good each is heard many times (up to once a minute) while it is near.
    Where vessels are heard about once each, a receiver only catches them now and then.
    """
    found = []
    async with engine.connect() as conn:
        for lon, lat in points:
            stmt = select(func.count(distinct(Position.mmsi)), func.count()).where(
                Position.received_at.between(at - window, at + window),
                func.ST_DWithin(Position.geom, func.geography(func.ST_Point(lon, lat, 4326)), radius_m),
            )
            vessels, reports = (await conn.execute(stmt)).one()
            found.append(Coverage(vessels, reports))
    return found


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
