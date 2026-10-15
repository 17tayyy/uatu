"""AIS analysis against the database: tracks and reference data in, events out."""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from geoalchemy2.shape import to_shape
from shapely import Geometry
from shapely.ops import transform as shapely_transform
from sqlalchemy import Text, and_, case, cast, delete, func, literal_column, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.ais.events import KNOT, Event, LocalProjection, StaticReport, Track, ais_day
from uatu.ais.stops import DEFAULT_STOPS, Ports, Stop, StopParams, Transit
from uatu.ais.stops import Port as NamedPort
from uatu.config import BBox
from uatu.db.models import (
    AisEvent,
    Infrastructure,
    Port,
    Position,
    SanctionedVessel,
    SarDetection,
    ShipCompany,
    StraitTransit,
    Vessel,
    VesselRisk,
    VesselStop,
    WatchedVessel,
)
from uatu.db.models import StaticReport as Report

# Events that span time and are re-detected on every run: a new detection that overlaps (or
# follows within this gap) one already stored extends it instead of adding another.
LISTED = {"sanctioned_vessel", "sanctioned_owner", "watchlisted_vessel", "watched_vessel"}
# What AIS can't make true: told once per visit too.
FALSE_AIS = {"position_on_land", "shared_mmsi", "odd_mmsi", "emergency_beacon"}
# listed_stop is not one: each stop is its own event, keyed by the stop's first report.
SPANNING = {"ais_gap", "encounter", "loitering", "infrastructure_loitering", *LISTED, *FALSE_AIS}
MERGE_GAP = timedelta(minutes=30)
# A listed vessel's visit lasts until it has been gone this long: one alert per visit, even
# across midnight or a stretch with no reception.
MERGE_GAPS = dict.fromkeys(LISTED | FALSE_AIS, timedelta(hours=12))


async def tracks(engine: AsyncEngine, start: datetime, end: datetime, bbox: BBox) -> list[Track]:
    lat_min, lon_min, lat_max, lon_max = bbox
    stmt = (
        select(
            Position.mmsi,
            Position.received_at,
            Position.lat,
            Position.lon,
            Position.sog,
            Position.cog,
            Position.nav_status,
        )
        .where(
            Position.received_at.between(start, end),
            Position.lat.between(lat_min, lat_max),
            Position.lon.between(lon_min, lon_max),
        )
        .order_by(Position.mmsi, Position.received_at)
    )
    by_vessel: dict[int, list[tuple]] = defaultdict(list)
    async with engine.connect() as conn:
        for mmsi, t, lat, lon, sog, cog, nav in await conn.execute(stmt):
            by_vessel[mmsi].append(
                (
                    t.timestamp(),
                    lat,
                    lon,
                    np.nan if sog is None else sog,
                    np.nan if cog is None else cog,
                    np.nan if nav is None else nav,
                )
            )
    result = []
    for mmsi, rows in by_vessel.items():
        t, lat, lon, sog, cog, nav = (np.array(column, dtype=float) for column in zip(*rows, strict=True))
        result.append(Track(mmsi, t, lat, lon, sog, cog, nav))
    return result


async def silences(engine: AsyncEngine, since: datetime) -> list[tuple[int, float, float]]:
    """(mmsi, lon, lat) where AIS gaps began since `since`, Uatu's own outages left out."""
    stmt = select(AisEvent.mmsi, AisEvent.lon, AisEvent.lat).where(
        AisEvent.kind == "ais_gap", AisEvent.start_at >= since, ~AisEvent.details.has_key("uatu_blind")
    )
    async with engine.connect() as conn:
        return [(m, lon, lat) for m, lon, lat in await conn.execute(stmt)]


async def stopped_places(
    engine: AsyncEngine, since: datetime, cell_deg: float = 0.01
) -> list[tuple[int, float, float]]:
    """(mmsi, lon, lat) of each ~1 km cell where a vessel sat stopped (< 1 kn, 3+ reports) since `since`."""
    lon_cell, lat_cell = func.round(Position.lon / cell_deg), func.round(Position.lat / cell_deg)
    stmt = (
        select(Position.mmsi, lon_cell * cell_deg, lat_cell * cell_deg)
        .where(Position.received_at >= since, Position.sog <= 1.0)
        .group_by(Position.mmsi, lon_cell, lat_cell)
        .having(func.count() >= 3)
    )
    async with engine.connect() as conn:
        return [(mmsi, float(lon), float(lat)) for mmsi, lon, lat in await conn.execute(stmt)]


async def recording_gaps(
    engine: AsyncEngine, since: datetime, min_duration: timedelta
) -> list[tuple[datetime, datetime]]:
    """Periods since `since` when the recorder received nothing (Uatu or its connection down)."""
    stmt = text(
        "SELECT gap_start, gap_end FROM ingest_gaps WHERE gap_end >= :since AND duration >= :min ORDER BY gap_start"
    )
    async with engine.connect() as conn:
        return [(a, b) for a, b in await conn.execute(stmt, {"since": since, "min": min_duration})]


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
        .ext(distinct_on(Position.mmsi))
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


def _seen(start: datetime, end: datetime) -> tuple[Any, Any]:
    """(first report, last position) of each Vessel in the window, to select alongside it."""
    last = (
        select(Position.lat, Position.lon, Position.received_at)
        .where(Position.mmsi == Vessel.mmsi, Position.received_at.between(start, end))
        .order_by(Position.received_at.desc())
        .limit(1)
        .lateral("last")
    )
    first_seen = (
        select(func.min(Position.received_at))
        .where(Position.mmsi == Vessel.mmsi, Position.received_at.between(start, end))
        .scalar_subquery()
    )
    return first_seen, last


async def sanctioned_present(engine: AsyncEngine, start: datetime, end: datetime) -> list[Event]:
    """Sanctioned vessels (by IMO, or by an MMSI the lists give for them) seen in the window."""
    first_seen, last = _seen(start, end)
    by_mmsi = func.strpos(SanctionedVessel.mmsis, cast(Vessel.mmsi, Text)) > 0
    stmt = (
        select(
            first_seen.label("first_seen"),
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
        events.append(Event("sanctioned_vessel", severity, r.mmsi, r.first_seen, r.lat, r.lon, details, r.received_at))
    return events


async def watched_present(engine: AsyncEngine, start: datetime, end: datetime) -> list[Event]:
    """Vessels on the watch list (by IMO, MMSI or name) seen in the window: an alert each visit."""
    first_seen, last = _seen(start, end)
    matched_by = case(
        (WatchedVessel.imo == Vessel.imo, literal_column("'imo'")),
        (WatchedVessel.mmsi == Vessel.mmsi, literal_column("'mmsi'")),
        else_=literal_column("'name'"),
    )
    stmt = (
        select(
            first_seen.label("first_seen"),
            Vessel.mmsi,
            Vessel.name,
            WatchedVessel.id.label("watch_id"),
            WatchedVessel.note,
            matched_by.label("matched_by"),
            last.c.lat,
            last.c.lon,
            last.c.received_at,
        )
        .join(
            WatchedVessel,
            or_(
                WatchedVessel.imo == Vessel.imo,
                WatchedVessel.mmsi == Vessel.mmsi,
                func.upper(WatchedVessel.name) == func.upper(Vessel.name),
            ),
        )
        .join(last, true())
        .order_by(Vessel.mmsi, WatchedVessel.id)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    events = {}  # one per vessel, though it match several entries
    for r in rows:
        details = {"name": r.name, "note": r.note, "watch_id": r.watch_id, "matched_by": r.matched_by}
        events.setdefault(
            r.mmsi, Event("watched_vessel", 3, r.mmsi, r.first_seen, r.lat, r.lon, details, r.received_at)
        )
    return list(events.values())


async def add_watch(engine: AsyncEngine, note: str, imo: int | None, mmsi: int | None, name: str | None) -> int:
    async with engine.begin() as conn:
        return await conn.scalar(
            pg_insert(WatchedVessel).values(imo=imo, mmsi=mmsi, name=name, note=note).returning(WatchedVessel.id)
        )


async def watches(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(select(WatchedVessel).order_by(WatchedVessel.id))).all())


async def remove_watch(engine: AsyncEngine, watch_id: int) -> bool:
    async with engine.begin() as conn:
        return (await conn.execute(delete(WatchedVessel).where(WatchedVessel.id == watch_id))).rowcount > 0


async def network_present(engine: AsyncEngine, start: datetime, end: datetime) -> list[Event]:
    """Vessels off the sanctions lists seen in the window that their companies or other lists flag.

    A sanctioned owner or manager is an alert; the rest (see sanctions.assess) is notable.
    """
    first_seen, last = _seen(start, end)
    stmt = (
        select(
            first_seen.label("first_seen"),
            Vessel.mmsi,
            Vessel.name,
            Vessel.imo,
            VesselRisk.level,
            VesselRisk.reasons,
            last.c.lat,
            last.c.lon,
            last.c.received_at,
        )
        .join(VesselRisk, VesselRisk.imo == Vessel.imo)
        .join(last, true())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    events = []
    for r in rows:
        kind, severity = ("sanctioned_owner", 3) if r.level == "sanctioned_owner" else ("watchlisted_vessel", 2)
        details = {"name": r.name, "imo": r.imo, "reasons": r.reasons}
        events.append(Event(kind, severity, r.mmsi, r.first_seen, r.lat, r.lon, details, r.received_at))
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
                gap = MERGE_GAPS.get(e.kind, MERGE_GAP)
                existing = (
                    await conn.execute(
                        select(AisEvent.id, AisEvent.start_at, AisEvent.end_at)
                        .where(
                            AisEvent.kind == e.kind,
                            AisEvent.mmsi == e.mmsi,
                            AisEvent.other_mmsi.is_not_distinct_from(e.other_mmsi),
                            AisEvent.start_at <= (e.end or e.start) + gap,
                            or_(AisEvent.end_at.is_(None), AisEvent.end_at >= e.start - gap),
                        )
                        .order_by(AisEvent.start_at)
                        .limit(1)
                    )
                ).first()
                if existing is not None:
                    await conn.execute(
                        update(AisEvent)
                        .where(AisEvent.id == existing.id)
                        .values(
                            end_at=e.end,
                            # The latest detection knows more (longer history, e.g. of waiting areas).
                            severity=e.severity,
                            lat=e.lat,
                            lon=e.lon,
                            details=_extended(e, existing.start_at),
                            updated_at=func.now(),
                        )
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


async def reference_loaded(engine: AsyncEngine) -> tuple[bool, bool, bool]:
    """(sanctions and the companies behind vessels loaded, infrastructure loaded, ports loaded)."""
    async with engine.connect() as conn:
        sanctions = await conn.scalar(select(func.count()).select_from(SanctionedVessel))
        companies = await conn.scalar(select(func.count()).select_from(ShipCompany))
        infrastructure = await conn.scalar(select(func.count()).select_from(Infrastructure))
        ports = await conn.scalar(select(func.count()).select_from(Port))
    return bool(sanctions and companies), bool(infrastructure), bool(ports)


# --- stops and transits ----------------------------------------------------------------------------


async def ports(engine: AsyncEngine) -> Ports:
    async with engine.connect() as conn:
        rows = (await conn.execute(select(Port.id, Port.name, Port.country, Port.lon, Port.lat))).all()
    return Ports([NamedPort(*row) for row in rows])


async def save_stops(
    engine: AsyncEngine, stops: list[Stop], zone: str | None, p: StopParams = DEFAULT_STOPS
) -> list[Stop]:
    """Store stops, extending the one a vessel was already found at rather than adding another (each
    analysis sees a few hours: a stop of days is seen in pieces). A piece is the same stop if it
    overlaps it in time, or follows it closely where drifting could have taken the vessel. The
    stops, from their first report."""
    gap = timedelta(seconds=p.max_silence_s)
    kept = []
    async with engine.begin() as conn:
        for s in stops:
            values = {
                "kind": s.kind,
                "lat": s.lat,
                "lon": s.lon,
                "coast_km": None if np.isnan(s.coast_m) else round(s.coast_m / 1000, 1),
                "port_id": s.place.id if s.place else None,
                "port_km": round(s.place_m / 1000, 1) if s.place_m is not None else None,
                "zone": zone,
            }
            candidates = (
                await conn.execute(
                    select(VesselStop.id, VesselStop.start_at, VesselStop.end_at, VesselStop.lat, VesselStop.lon)
                    .where(
                        VesselStop.mmsi == s.mmsi,
                        VesselStop.start_at <= s.end + gap,
                        VesselStop.end_at >= s.start - gap,
                    )
                    .order_by(VesselStop.start_at)
                )
            ).all()
            same = next((c for c in candidates if _same_stop(s, c, p)), None)
            if same is None:
                await conn.execute(pg_insert(VesselStop).values(mmsi=s.mmsi, start_at=s.start, end_at=s.end, **values))
                kept.append(s)
                continue
            start, end = min(same.start_at, s.start), max(same.end_at, s.end)
            await conn.execute(
                update(VesselStop)
                .where(VesselStop.id == same.id)
                .values(start_at=start, end_at=end, updated_at=func.now(), **values)
            )
            kept.append(replace(s, start=start, end=end))
    return kept


def _same_stop(s: Stop, stored: Any, p: StopParams) -> bool:
    """Whether a stop just found is (a piece of) one stored, as detect_stops would have joined them."""
    if stored.start_at <= s.end and stored.end_at >= s.start:
        return True  # a vessel is at one stop at a time
    middle = (s.start + (s.end - s.start) / 2) - (stored.start_at + (stored.end_at - stored.start_at) / 2)
    apart = np.hypot(*LocalProjection(s.lon, s.lat).xy(stored.lon, stored.lat))
    return apart <= max(p.jitter_m, p.drift_sog * KNOT * abs(middle.total_seconds()))  # no faster than drifting


async def clear_stops(engine: AsyncEngine, since: datetime) -> None:
    """Forget the stops and transits found since then, to find them again."""
    async with engine.begin() as conn:
        await conn.execute(delete(VesselStop).where(VesselStop.end_at >= since))
        await conn.execute(delete(StraitTransit).where(StraitTransit.at >= since))
        await conn.execute(delete(AisEvent).where(AisEvent.kind == "listed_stop", AisEvent.start_at >= since))


async def stops_since(engine: AsyncEngine, since: datetime, listed_only: bool = False) -> list[Any]:
    """Stops going on since then, with the vessel's name and whether it is sanctioned or watchlisted."""
    listed = case((SanctionedVessel.imo.is_not(None), literal_column("'sanctioned'")), else_=VesselRisk.level).label(
        "listed"
    )
    stop = (VesselStop.mmsi, VesselStop.kind, VesselStop.start_at, VesselStop.end_at, VesselStop.lat, VesselStop.lon)
    stmt = (
        select(*stop, VesselStop.port_km, Vessel.name, Port.name.label("port"), listed)
        .outerjoin(Vessel, Vessel.mmsi == VesselStop.mmsi)
        .outerjoin(Port, Port.id == VesselStop.port_id)
        .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
        .outerjoin(VesselRisk, VesselRisk.imo == Vessel.imo)
        .where(VesselStop.end_at >= since)
        .order_by(VesselStop.start_at)
    )
    if listed_only:
        stmt = stmt.where(or_(SanctionedVessel.imo.is_not(None), VesselRisk.imo.is_not(None)))
    async with engine.connect() as conn:
        return list((await conn.execute(stmt)).all())


async def save_transits(engine: AsyncEngine, transits: list[Transit]) -> int:
    """Store Strait transits; one seen again (between the same two reports) is the same."""
    if not transits:
        return 0
    rows = [{"mmsi": t.mmsi, "at": t.at, "direction": t.direction, "lat": t.lat} for t in transits]
    async with engine.begin() as conn:
        result = await conn.execute(pg_insert(StraitTransit).values(rows).on_conflict_do_nothing())
    return result.rowcount


async def listed(engine: AsyncEngine, mmsis: set[int]) -> dict[int, tuple[str, str | None]]:
    """(sanctioned | sanctioned_owner | watchlisted, name) of those vessels that are, by IMO number."""
    if not mmsis:
        return {}
    stmt = (
        select(Vessel.mmsi, Vessel.name, SanctionedVessel.imo.label("sanctioned"), VesselRisk.level)
        .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
        .outerjoin(VesselRisk, VesselRisk.imo == Vessel.imo)
        .where(Vessel.mmsi.in_(mmsis), or_(SanctionedVessel.imo.is_not(None), VesselRisk.imo.is_not(None)))
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return {r.mmsi: ("sanctioned" if r.sanctioned else r.level, r.name) for r in rows}
