"""AIS analysis against the database: tracks and reference data in, events out."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from geoalchemy2.shape import to_shape
from shapely import Geometry
from shapely.ops import transform as shapely_transform
from sqlalchemy import Text, and_, cast, func, literal_column, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.ais.events import Event, LocalProjection, StaticReport, Track, ais_day
from uatu.config import BBox
from uatu.db.models import AisEvent, Infrastructure, Position, SanctionedVessel, SarDetection, Vessel
from uatu.db.models import StaticReport as Report

# Events that span time and are re-detected on every run: a new detection that overlaps (or
# follows within this gap) one already stored extends it instead of adding another.
SPANNING = {"ais_gap", "encounter", "loitering", "infrastructure_loitering"}
MERGE_GAP = timedelta(minutes=30)


async def tracks(engine: AsyncEngine, start: datetime, end: datetime, bbox: BBox) -> list[Track]:
    lat_min, lon_min, lat_max, lon_max = bbox
    stmt = (
        select(Position.mmsi, Position.received_at, Position.lat, Position.lon, Position.sog, Position.cog)
        .where(
            Position.received_at.between(start, end),
            Position.lat.between(lat_min, lat_max),
            Position.lon.between(lon_min, lon_max),
        )
        .order_by(Position.mmsi, Position.received_at)
    )
    by_vessel: dict[int, list[tuple]] = defaultdict(list)
    async with engine.connect() as conn:
        for mmsi, t, lat, lon, sog, cog in await conn.execute(stmt):
            by_vessel[mmsi].append(
                (t.timestamp(), lat, lon, np.nan if sog is None else sog, np.nan if cog is None else cog)
            )
    result = []
    for mmsi, rows in by_vessel.items():
        t, lat, lon, sog, cog = (np.array(column, dtype=float) for column in zip(*rows, strict=True))
        result.append(Track(mmsi, t, lat, lon, sog, cog))
    return result


async def static_reports(engine: AsyncEngine, since: datetime) -> list[StaticReport]:
    stmt = (
        select(Report.mmsi, Report.received_at, Report.name, Report.imo, Report.callsign, Report.ship_type)
        .where(Report.received_at >= since)
        .order_by(Report.received_at)
    )
    async with engine.connect() as conn:
        return [StaticReport(*row) for row in await conn.execute(stmt)]


async def last_positions(engine: AsyncEngine, since: datetime) -> dict[int, tuple[float, float]]:
    stmt = (
        select(Position.mmsi, Position.lat, Position.lon)
        .where(Position.received_at >= since)
        .distinct(Position.mmsi)
        .order_by(Position.mmsi, Position.received_at.desc())
    )
    async with engine.connect() as conn:
        return {mmsi: (lat, lon) for mmsi, lat, lon in await conn.execute(stmt)}


async def infrastructure_lines(engine: AsyncEngine, proj: LocalProjection) -> list[tuple[str, str, Geometry]]:
    """In-service cables and pipelines, in the projection's metres."""
    stmt = select(Infrastructure.kind, Infrastructure.name, Infrastructure.geom).where(
        Infrastructure.kind.in_(["telecom_cable", "pipeline"]),
        or_(Infrastructure.status.is_(None), Infrastructure.status != "abandoned"),
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()

    def to_metres(lon: Any, lat: Any, z: Any = None) -> tuple:
        return proj.xy(np.asarray(lon), np.asarray(lat))

    return [(kind, name, shapely_transform(to_metres, to_shape(geom))) for kind, name, geom in rows]


def valid_imo(imo: int | None) -> bool:
    """IMO numbers carry a check digit; transponders are often set up with made-up ones."""
    if imo is None or not 1_000_000 <= imo <= 9_999_999:
        return False
    digits = [int(d) for d in str(imo)]
    return sum(d * (7 - i) for i, d in enumerate(digits[:6])) % 10 == digits[6]


async def sanctioned_present(engine: AsyncEngine, start: datetime, end: datetime) -> list[Event]:
    """Sanctioned vessels (by IMO, or by an MMSI the lists give for them) seen in the window."""
    last = (
        select(Position.lat, Position.lon, Position.received_at)
        .where(Position.mmsi == Vessel.mmsi, Position.received_at.between(start, end))
        .order_by(Position.received_at.desc())
        .limit(1)
        .lateral("last")
    )
    by_mmsi = func.strpos(SanctionedVessel.mmsis, cast(Vessel.mmsi, Text)) > 0
    stmt = (
        select(
            Vessel.mmsi,
            Vessel.name,
            Vessel.imo,
            SanctionedVessel.imo.label("listed_imo"),
            SanctionedVessel.name.label("listed_name"),
            SanctionedVessel.programs,
            (SanctionedVessel.imo == Vessel.imo).label("by_imo"),
            last.c.lat,
            last.c.lon,
            last.c.received_at,
        )
        .join(SanctionedVessel, or_(SanctionedVessel.imo == Vessel.imo, by_mmsi))
        .join(last, true())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    events = []
    for r in rows:
        details = {
            "name": r.name,
            "imo": r.listed_imo,
            "listed_as": r.listed_name,
            "programs": r.programs,
            "matched_by": "imo" if r.by_imo else "mmsi",
        }
        severity = 3 if r.by_imo else 2  # MMSIs get reassigned; an IMO stays with the hull
        events.append(
            Event("sanctioned_vessel", severity, r.mmsi, ais_day(r.received_at), r.lat, r.lon, details, r.received_at)
        )
    return events


async def shared_imos(engine: AsyncEngine, start: datetime, end: datetime) -> list[Event]:
    """Two MMSIs seen in the window announcing the same (valid) IMO: one of them is not who it says."""
    seen = (
        select(Position.mmsi, func.max(Position.received_at).label("at"))
        .where(Position.received_at.between(start, end))
        .group_by(Position.mmsi)
        .subquery()
    )
    a, b = Vessel.__table__.alias("a"), Vessel.__table__.alias("b")
    seen_a, seen_b = seen.alias("seen_a"), seen.alias("seen_b")
    stmt = (
        select(a.c.mmsi, b.c.mmsi.label("other"), a.c.imo, a.c.name, b.c.name.label("other_name"), seen_a.c.at)
        .join(b, and_(a.c.imo == b.c.imo, a.c.mmsi < b.c.mmsi))
        .join(seen_a, seen_a.c.mmsi == a.c.mmsi)
        .join(seen_b, seen_b.c.mmsi == b.c.mmsi)
        .where(a.c.imo.is_not(None))
    )
    async with engine.connect() as conn:
        rows = [r for r in (await conn.execute(stmt)).all() if valid_imo(r.imo)]
        where = await last_positions(engine, start) if rows else {}
    events = []
    for r in rows:
        lat, lon = where.get(r.mmsi, (np.nan, np.nan))
        details = {"imo": r.imo, "name": r.name, "other_name": r.other_name}
        events.append(Event("shared_imo", 2, r.mmsi, ais_day(r.at), lat, lon, details, r.at, r.other))
    return [e for e in events if not np.isnan(e.lat)]


async def close_resumed_gaps(engine: AsyncEngine) -> int:
    """Close open gaps whose vessel has transmitted again, however long ago they started."""
    resumed = (
        select(func.min(Position.received_at))
        .where(Position.mmsi == AisEvent.mmsi, Position.received_at > AisEvent.start_at)
        .scalar_subquery()
    )
    stmt = (
        update(AisEvent)
        .where(AisEvent.kind == "ais_gap", AisEvent.end_at.is_(None), resumed.is_not(None))
        .values(end_at=resumed, updated_at=func.now())
    )
    async with engine.begin() as conn:
        return (await conn.execute(stmt)).rowcount


async def save(engine: AsyncEngine, events: list[Event]) -> list[Event]:
    """Store events, extending ongoing ones rather than duplicating them; return the new ones."""
    new = []
    async with engine.begin() as conn:
        for e in events:
            if e.kind in SPANNING:
                existing = (
                    await conn.execute(
                        select(AisEvent.id, AisEvent.start_at, AisEvent.end_at)
                        .where(
                            AisEvent.kind == e.kind,
                            AisEvent.mmsi == e.mmsi,
                            AisEvent.other_mmsi.is_not_distinct_from(e.other_mmsi),
                            AisEvent.start_at <= (e.end or e.start) + MERGE_GAP,
                            or_(AisEvent.end_at.is_(None), AisEvent.end_at >= e.start - MERGE_GAP),
                        )
                        .order_by(AisEvent.start_at)
                        .limit(1)
                    )
                ).first()
                if existing is not None:
                    await conn.execute(
                        update(AisEvent)
                        .where(AisEvent.id == existing.id)
                        .values(end_at=e.end, details=_extended(e, existing.start_at), updated_at=func.now())
                    )
                    continue
            stmt = (
                pg_insert(AisEvent)
                .values(
                    key=e.key,
                    kind=e.kind,
                    severity=e.severity,
                    mmsi=e.mmsi,
                    other_mmsi=e.other_mmsi,
                    start_at=e.start,
                    end_at=e.end,
                    lat=e.lat,
                    lon=e.lon,
                    details=e.details,
                )
                .on_conflict_do_update(
                    index_elements=[AisEvent.key],
                    set_={"end_at": e.end, "details": e.details, "updated_at": func.now()},
                )
                .returning(literal_column("xmax = 0"))  # true when the row was inserted, not updated
            )
            if (await conn.execute(stmt)).scalar():
                new.append(e)
    return new


def _extended(e: Event, start: datetime) -> dict:
    """Details of an event extended back to an earlier stored start."""
    if e.end is None:
        return e.details
    span = (e.end - min(start, e.start)).total_seconds()
    details = dict(e.details)
    if "hours" in details:
        details["hours"] = round(span / 3600, 1)
    if "minutes" in details:
        details["minutes"] = round(span / 60)
    return details


async def vessel_names(engine: AsyncEngine, mmsis: set[int]) -> dict[int, str]:
    if not mmsis:
        return {}
    async with engine.connect() as conn:
        rows = await conn.execute(select(Vessel.mmsi, Vessel.name).where(Vessel.mmsi.in_(mmsis)))
        return {mmsi: name for mmsi, name in rows if name}


# --- incident correlation --------------------------------------------------------------------------


@dataclass(frozen=True)
class Around:
    vessels: list[dict[str, Any]]  # every vessel with AIS within the radius and time window
    events: list[dict[str, Any]]  # AIS events there and then
    detections: list[dict[str, Any]]  # SAR detections there and then


async def around(
    engine: AsyncEngine, lat: float, lon: float, at: datetime, radius_m: float, window: timedelta
) -> Around:
    """Who was near (lat, lon) around time `at`: the question asked after any incident at sea."""
    here = func.geography(func.ST_Point(lon, lat, 4326))
    start, end = at - window, at + window
    distance = func.ST_Distance(Position.geom, here)
    near = (
        select(
            Position.mmsi,
            func.min(distance).label("closest_m"),
            func.count().label("reports"),
            func.min(Position.received_at).label("first"),
            func.max(Position.received_at).label("last"),
        )
        .where(Position.received_at.between(start, end), func.ST_DWithin(Position.geom, here, radius_m))
        .group_by(Position.mmsi)
        .subquery()
    )
    vessels_stmt = (
        select(
            near,
            Vessel.name,
            Vessel.imo,
            Vessel.ship_type,
            Vessel.length_m,
            SanctionedVessel.programs.label("sanctions"),
        )
        .outerjoin(Vessel, Vessel.mmsi == near.c.mmsi)
        .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
        .order_by(near.c.closest_m)
    )
    events_stmt = (
        select(AisEvent)
        .where(
            func.ST_DWithin(AisEvent.geom, here, radius_m),
            AisEvent.start_at <= end,
            or_(AisEvent.end_at.is_(None), AisEvent.end_at >= start),
        )
        .order_by(AisEvent.start_at)
    )
    detections_stmt = (
        select(
            SarDetection.seen_at,
            SarDetection.status,
            SarDetection.length_m,
            SarDetection.mmsi,
            func.ST_Distance(SarDetection.geom, here).label("away_m"),
        )
        .where(SarDetection.seen_at.between(start, end), func.ST_DWithin(SarDetection.geom, here, radius_m))
        .order_by(SarDetection.seen_at)
    )
    async with engine.connect() as conn:
        vessels = [dict(r._mapping) for r in await conn.execute(vessels_stmt)]
        events = [dict(r._mapping) for r in await conn.execute(events_stmt)]
        detections = [dict(r._mapping) for r in await conn.execute(detections_stmt)]
    return Around(vessels, events, detections)


async def notable_since(engine: AsyncEngine, since: datetime, min_severity: int) -> list[Event]:
    """Stored events of at least `min_severity` that started or were still going on since `since`."""
    stmt = (
        select(AisEvent)
        .where(AisEvent.severity >= min_severity, or_(AisEvent.end_at.is_(None), AisEvent.end_at >= since))
        .order_by(AisEvent.severity.desc(), AisEvent.start_at)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [
        Event(r.kind, r.severity, r.mmsi, r.start_at, r.lat, r.lon, r.details, r.end_at, r.other_mmsi) for r in rows
    ]


async def reference_loaded(engine: AsyncEngine) -> tuple[bool, bool]:
    """(sanctions loaded, infrastructure loaded)."""
    async with engine.connect() as conn:
        sanctions = await conn.scalar(select(func.count()).select_from(SanctionedVessel))
        infrastructure = await conn.scalar(select(func.count()).select_from(Infrastructure))
    return bool(sanctions), bool(infrastructure)
