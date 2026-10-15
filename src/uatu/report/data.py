"""Everything a report says, gathered from the database for one period."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from uatu.ais.sanctions import explain
from uatu.config import BBox, Zone
from uatu.db.models import (
    AisEvent,
    Port,
    Position,
    RecordedZone,
    SanctionedVessel,
    StraitTransit,
    Vessel,
    VesselRisk,
    VesselStop,
)
from uatu.web.geo import inside
from uatu.web.passes.schemas import PassSummary
from uatu.web.passes.service import get_pass, list_passes
from uatu.web.vessels.labels import ship_type

MADRID = ZoneInfo("Europe/Madrid")
CELL_DEG = 0.02
CANDIDATES = ("went_silent", "unidentified")
# Unidentified radar detections are given to ~10 km: they may be small boats (migrants, smugglers)
# whose position is nobody's business in a public report.
UNIDENTIFIED_DECIMALS = 1


@dataclass(frozen=True)
class Heard:
    zone: str  # its name
    reports: int
    vessels: int


@dataclass(frozen=True)
class Candidate:
    pass_id: str
    seen_at: datetime
    kind: str  # went_silent | unidentified
    length_m: float
    lon: float
    lat: float
    suspect: str | None  # the vessel that went silent and could be there
    silent_minutes: float | None


@dataclass
class Listed:
    mmsi: int
    name: str | None
    imo: int
    ship_type: str | None
    length_m: int | None
    status: str  # sanctioned | sanctioned_owner | watchlisted
    why: list[str]
    first_heard: datetime
    last_heard: datetime
    transits: list[tuple[datetime, str]] = field(default_factory=list)
    stops: list[dict[str, Any]] = field(default_factory=list)
    track: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class Notable:
    kind: str
    severity: int
    start: datetime
    end: datetime | None
    mmsi: int
    name: str | None
    lat: float
    lon: float
    details: dict[str, Any]


@dataclass
class Report:
    start: datetime
    end: datetime
    zones: list[Zone]  # analysed: radar, events, stops
    recorded: list[Zone]  # AIS recorded
    heard: list[Heard]
    vessels_heard: int
    outages: list[tuple[datetime, datetime]]
    passes: list[PassSummary]
    candidates: list[Candidate]
    listed: list[Listed]
    notable: list[Notable]
    transits_by_day: list[tuple[str, int, int]]  # Madrid date, east, west
    stops_by_place: list[dict[str, Any]]
    density: list[tuple[float, float, int]]  # cell centre lon, lat, vessel-hours
    recording_since: datetime | None = None  # when Uatu began recording, if within the period

    @property
    def recording_start(self) -> datetime:
        return max(self.start, self.recording_since or self.start)

    @property
    def recorded_hours(self) -> float:
        """Hours of the period Uatu was recording: from when it began, less its outages."""
        lost = sum(max((min(b, self.end) - max(a, self.recording_start)).total_seconds(), 0) for a, b in self.outages)
        return ((self.end - self.recording_start).total_seconds() - lost) / 3600


def span(zones: list[Zone]) -> BBox:
    return (
        min(z.bbox[0] for z in zones),
        min(z.bbox[1] for z in zones),
        max(z.bbox[2] for z in zones),
        max(z.bbox[3] for z in zones),
    )


async def gather(
    engine: AsyncEngine, start: datetime, end: datetime, zones: list[Zone], all_zones: dict[str, Zone], data_dir: Path
) -> Report:
    async with engine.connect() as conn:
        recorded_keys = list(await conn.scalars(select(RecordedZone.key).order_by(RecordedZone.since)))
        since = await conn.scalar(select(func.min(RecordedZone.since)))
    recorded = [all_zones[k] for k in recorded_keys if k in all_zones] or zones
    coast = span(recorded)
    in_period = Position.received_at.between(start, end)

    async with engine.connect() as conn:
        heard = []
        for z in recorded:
            reports, vessels = (
                await conn.execute(
                    select(func.count(), func.count(distinct(Position.mmsi))).where(
                        in_period, inside(Position.lat, Position.lon, z.bbox)
                    )
                )
            ).one()
            heard.append(Heard(z.name, reports, vessels))
        vessels_heard = await conn.scalar(
            select(func.count(distinct(Position.mmsi))).where(in_period, inside(Position.lat, Position.lon, coast))
        )
        outages = [
            (a, b)
            for a, b in await conn.execute(
                text(
                    "SELECT gap_start, gap_end FROM ingest_gaps"
                    " WHERE gap_start < :end AND gap_end > :start AND duration >= :min ORDER BY gap_start"
                ),
                {"start": start, "end": end, "min": timedelta(minutes=5)},
            )
        ]

    passes, candidates = await _radar(engine, start, end, zones, data_dir)
    listed = await _listed(engine, start, end, coast)
    async with engine.connect() as conn:
        notable = await _notable(conn, start, end)
        transits = await _transits_by_day(conn, start, end)
        stops = await _stops_by_place(conn, start, end, [z.key for z in zones])
        density = await _density(conn, start, end, coast)
    return Report(
        start, end, zones, recorded, heard, vessels_heard or 0, outages, passes, candidates, listed, notable,
        transits, stops, density, since,
    )  # fmt: skip


async def _radar(
    engine: AsyncEngine, start: datetime, end: datetime, zones: list[Zone], data_dir: Path
) -> tuple[list[PassSummary], list[Candidate]]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        passes = [
            p for p in await list_passes(db, data_dir, [z.key for z in zones], 500) if start <= p.acquired_at < end
        ]
        candidates = []
        for p in passes:
            if not p.candidates:
                continue
            detail = await get_pass(db, data_dir, p.id)
            for f in detail.detections_geojson.features:
                d = f.properties
                if d.kind not in CANDIDATES:
                    continue
                lon, lat = f.geometry.coordinates
                decimals = UNIDENTIFIED_DECIMALS if d.kind == "unidentified" else 2
                candidates.append(
                    Candidate(
                        pass_id=p.id,
                        seen_at=d.seen_at,
                        kind=d.kind,
                        length_m=d.length_m,
                        lon=round(lon, decimals),
                        lat=round(lat, decimals),
                        suspect=d.suspect_name or (str(d.suspected_mmsi) if d.suspected_mmsi else None),
                        silent_minutes=d.suspect_silent_minutes,
                    )
                )
    return sorted(passes, key=lambda p: p.acquired_at), candidates


async def _listed(engine: AsyncEngine, start: datetime, end: datetime, coast: BBox) -> list[Listed]:
    """Sanctioned and watchlisted vessels heard in the period, with their crossings, stops and track."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    Vessel.mmsi,
                    Vessel.name,
                    Vessel.imo,
                    Vessel.ship_type,
                    Vessel.length_m,
                    SanctionedVessel.name.label("listed_as"),
                    SanctionedVessel.programs,
                    VesselRisk.level,
                    VesselRisk.reasons,
                    func.min(Position.received_at).label("first"),
                    func.max(Position.received_at).label("last"),
                )
                .join(Position, Position.mmsi == Vessel.mmsi)
                .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
                .outerjoin(VesselRisk, VesselRisk.imo == Vessel.imo)
                .where(
                    Position.received_at.between(start, end),
                    inside(Position.lat, Position.lon, coast),
                    or_(SanctionedVessel.imo.is_not(None), VesselRisk.imo.is_not(None)),
                )
                .group_by(Vessel.mmsi, SanctionedVessel.imo, VesselRisk.imo)
                .order_by(func.min(Position.received_at))
            )
        ).all()
        listed = {}
        for r in rows:
            if r.listed_as is not None:
                programs = f" ({r.programs.replace(';', ', ')})" if r.programs else ""
                status, why = "sanctioned", [f"en listas de sanciones como {r.listed_as}{programs}"]
            else:
                status, why = r.level, [explain(reason) for reason in r.reasons]
            listed[r.mmsi] = Listed(
                r.mmsi, r.name, r.imo, ship_type(r.ship_type), r.length_m, status, why, r.first, r.last
            )
        if not listed:
            return []
        mmsis = list(listed)
        for t in await conn.execute(
            select(StraitTransit.mmsi, StraitTransit.at, StraitTransit.direction)
            .where(StraitTransit.mmsi.in_(mmsis), StraitTransit.at.between(start, end))
            .order_by(StraitTransit.at)
        ):
            listed[t.mmsi].transits.append((t.at, t.direction))
        for s in await conn.execute(
            select(VesselStop, Port.name.label("port"), Port.country)
            .outerjoin(Port, Port.id == VesselStop.port_id)
            .where(VesselStop.mmsi.in_(mmsis), VesselStop.start_at < end, VesselStop.end_at > start)
            .order_by(VesselStop.start_at)
        ):
            listed[s.mmsi].stops.append(
                {
                    "kind": s.kind,
                    "start": s.start_at,
                    "end": s.end_at,
                    "place": s.port,
                    "country": s.country,
                    "place_km": s.port_km,
                    "lon": s.lon,
                    "lat": s.lat,
                }
            )
        for p in await conn.execute(
            select(Position.mmsi, Position.lon, Position.lat)
            .where(Position.mmsi.in_(mmsis), Position.received_at.between(start, end))
            .order_by(Position.mmsi, Position.received_at)
        ):
            listed[p.mmsi].track.append((p.lon, p.lat))
    order = {"sanctioned": 0, "sanctioned_owner": 1, "watchlisted": 2}
    return sorted(listed.values(), key=lambda v: (order.get(v.status, 3), v.first_heard))


async def _notable(conn: Any, start: datetime, end: datetime) -> list[Notable]:
    rows = await conn.execute(
        select(AisEvent, Vessel.name)
        .outerjoin(Vessel, Vessel.mmsi == AisEvent.mmsi)
        .where(
            AisEvent.severity >= 2,
            AisEvent.start_at < end,
            or_(AisEvent.end_at.is_(None), AisEvent.end_at >= start),
        )
        .order_by(AisEvent.start_at)
    )
    return [
        Notable(
            r.kind, r.severity, r.start_at, r.end_at, r.mmsi, r.name or r.details.get("name"), r.lat, r.lon, r.details
        )
        for r in rows
    ]


async def _transits_by_day(conn: Any, start: datetime, end: datetime) -> list[tuple[str, int, int]]:
    days: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for at, direction in await conn.execute(
        select(StraitTransit.at, StraitTransit.direction).where(StraitTransit.at.between(start, end))
    ):
        days[at.astimezone(MADRID).date().isoformat()][0 if direction == "east" else 1] += 1
    return [(day, east, west) for day, (east, west) in sorted(days.items())]


async def _stops_by_place(conn: Any, start: datetime, end: datetime, zones: list[str]) -> list[dict[str, Any]]:
    """Per port (or none) and kind: stops, vessels and vessel-hours, within the period."""
    clipped = func.extract("epoch", func.least(VesselStop.end_at, end) - func.greatest(VesselStop.start_at, start))
    rows = await conn.execute(
        select(
            Port.name.label("place"),
            Port.country,
            VesselStop.kind,
            func.count().label("stops"),
            func.count(distinct(VesselStop.mmsi)).label("vessels"),
            (func.sum(clipped) / 3600).label("hours"),
        )
        .outerjoin(Port, Port.id == VesselStop.port_id)
        .where(VesselStop.start_at < end, VesselStop.end_at > start, VesselStop.zone.in_(zones))
        .group_by(Port.name, Port.country, VesselStop.kind)
        .order_by(func.sum(clipped).desc())
    )
    return [dict(r._mapping) for r in rows]


async def _density(conn: Any, start: datetime, end: datetime, coast: BBox) -> list[tuple[float, float, int]]:
    row_ = func.floor(Position.lat / CELL_DEG).label("row")
    col_ = func.floor(Position.lon / CELL_DEG).label("col")
    hours = func.count(distinct(tuple_(Position.mmsi, func.date_trunc("hour", Position.received_at))))
    rows = await conn.execute(
        select(row_, col_, hours.label("vessel_hours"))
        .where(Position.received_at.between(start, end), inside(Position.lat, Position.lon, coast))
        .group_by(row_, col_)
    )
    return [((r.col + 0.5) * CELL_DEG, (r.row + 0.5) * CELL_DEG, r.vessel_hours) for r in rows]
