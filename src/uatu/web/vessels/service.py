"""Vessels heard on AIS: who is out there now, and everything known about one of them."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, false, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.ais.analysis import describe
from uatu.ais.events import Event
from uatu.ais.sanctions import explain
from uatu.config import BBox
from uatu.db.models import (
    AisEvent,
    Port,
    Position,
    SanctionedVessel,
    SarDetection,
    SarScene,
    StraitTransit,
    Vessel,
    VesselRisk,
    VesselStop,
    WatchedVessel,
    WatchlistedVessel,
)
from uatu.web.companies.service import vessel_companies
from uatu.web.errors import NotFoundError
from uatu.web.events.schemas import EventProperties
from uatu.web.geo import inside
from uatu.web.passes.service import pass_id
from uatu.web.schemas import Feature, FeatureCollection, Geometry, point
from uatu.web.vessels.labels import flag, ship_type
from uatu.web.vessels.schemas import (
    LiveVesselProperties,
    RadarSighting,
    Risk,
    Sanction,
    StopOut,
    TransitOut,
    VesselDetail,
    VesselSummary,
    Watch,
    Watchlisting,
)


def _latest_positions(since: datetime, bbox: BBox) -> Any:
    """Each vessel's latest position within the zone since `since`."""
    return (
        select(Position)
        .where(Position.received_at >= since, inside(Position.lat, Position.lon, bbox))
        .ext(distinct_on(Position.mmsi))
        .order_by(Position.mmsi, Position.received_at.desc())
        .subquery()
    )


def _watched(mmsi: Any, imo: Any, name: Any) -> Any:
    """On one's own watch list, by any of the vessel's identifiers (columns, or values: None matches nothing)."""
    matches = [
        WatchedVessel.mmsi == mmsi if mmsi is not None else None,
        WatchedVessel.imo == imo if imo is not None else None,
        func.upper(WatchedVessel.name) == func.upper(name) if name is not None else None,
    ]
    return or_(false(), *(m for m in matches if m is not None))


def _live(row: Any) -> Feature[LiveVesselProperties]:
    return Feature(
        geometry=point(row.lon, row.lat),
        properties=LiveVesselProperties(
            mmsi=row.mmsi,
            name=row.name,
            ship_type=ship_type(row.ship_type),
            length_m=row.length_m,
            flag=flag(row.mmsi),
            sog=row.sog,
            cog=row.cog,
            heading=row.heading,
            received_at=row.received_at,
            sanctioned=row.programs is not None,
            risk=row.level,
            watched=row.watched,
        ),
    )


async def live_vessels(
    db: AsyncSession, bbox: BBox, minutes: float, limit: int
) -> FeatureCollection[LiveVesselProperties]:
    """The last position of every vessel heard in the zone in the last `minutes`."""
    latest = _latest_positions(datetime.now(UTC) - timedelta(minutes=minutes), bbox)
    position = (latest.c.mmsi, latest.c.lat, latest.c.lon, latest.c.sog, latest.c.cog, latest.c.heading)
    watched = select(WatchedVessel.id).where(_watched(latest.c.mmsi, Vessel.imo, Vessel.name)).exists()
    vessel = (Vessel.name, Vessel.ship_type, Vessel.length_m, SanctionedVessel.programs, VesselRisk.level)
    columns = (*position, latest.c.received_at, *vessel, watched.label("watched"))
    rows = await db.execute(
        select(*columns)
        .outerjoin(Vessel, Vessel.mmsi == latest.c.mmsi)
        .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
        .outerjoin(VesselRisk, VesselRisk.imo == Vessel.imo)
        .order_by(latest.c.mmsi)
        .limit(limit)
    )
    return FeatureCollection(features=[_live(r) for r in rows])


async def search_vessels(db: AsyncSession, query: str, limit: int) -> list[VesselSummary]:
    """Vessels whose name contains the query, or whose MMSI starts with it."""
    last_seen = (
        select(func.max(Position.received_at)).where(Position.mmsi == Vessel.mmsi).correlate(Vessel).scalar_subquery()
    )
    pattern = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await db.execute(
        select(Vessel.mmsi, Vessel.name, Vessel.ship_type, last_seen.label("last_seen_at"))
        .where(or_(Vessel.name.ilike(f"%{pattern}%"), cast(Vessel.mmsi, Text).like(f"{pattern}%")))
        .order_by(Vessel.name, Vessel.mmsi)
        .limit(limit)
    )
    return [
        VesselSummary(
            mmsi=r.mmsi, name=r.name, ship_type=ship_type(r.ship_type), flag=flag(r.mmsi), last_seen_at=r.last_seen_at
        )
        for r in rows
    ]


async def get_vessel(db: AsyncSession, mmsi: int, track_hours: float) -> VesselDetail:
    vessel = await db.scalar(select(Vessel).where(Vessel.mmsi == mmsi))
    heard = await db.scalar(select(func.count()).where(Position.mmsi == mmsi))
    if vessel is None and not heard:
        raise NotFoundError("Vessel")

    sanction, risk, watchlists, companies = None, None, [], []
    if vessel is not None and vessel.imo is not None:
        listed = await db.scalar(select(SanctionedVessel).where(SanctionedVessel.imo == vessel.imo))
        if listed is not None:
            sanction = Sanction(listed_as=listed.name, programs=listed.programs)
        if (flagged := await db.get(VesselRisk, vessel.imo)) is not None:
            risk = Risk(level=flagged.level, reasons=[explain(r) for r in flagged.reasons])
        watchlists = [
            Watchlisting(watchlist=w.watchlist, note=w.note)
            for w in await db.scalars(
                select(WatchlistedVessel)
                .where(WatchlistedVessel.imo == vessel.imo)
                .order_by(WatchlistedVessel.watchlist)
            )
        ]
        companies = await vessel_companies(db, vessel.imo)
    identifiers = _watched(mmsi, vessel.imo if vessel else None, vessel.name if vessel else None)
    watched = [
        Watch(note=w.note, added_at=w.added_at)
        for w in await db.scalars(select(WatchedVessel).where(identifiers).order_by(WatchedVessel.id))
    ]

    since = datetime.now(UTC) - timedelta(hours=track_hours)
    fixes = (
        await db.execute(
            select(Position.lon, Position.lat, Position.sog, Position.cog, Position.heading, Position.received_at)
            .where(Position.mmsi == mmsi, Position.received_at >= since)
            .order_by(Position.received_at)
        )
    ).all()
    last = (
        await db.execute(select(Position).where(Position.mmsi == mmsi).order_by(Position.received_at.desc()).limit(1))
    ).scalar()

    last_position = None
    if last is not None:
        last_position = Feature(
            geometry=point(last.lon, last.lat),
            properties=LiveVesselProperties(
                mmsi=mmsi,
                name=vessel.name if vessel else None,
                ship_type=ship_type(vessel.ship_type) if vessel else None,
                length_m=vessel.length_m if vessel else None,
                flag=flag(mmsi),
                sog=last.sog,
                cog=last.cog,
                heading=last.heading,
                received_at=last.received_at,
                sanctioned=sanction is not None,
                risk=risk.level if risk else None,
                watched=bool(watched),
            ),
        )

    events = (
        await db.scalars(
            select(AisEvent)
            .where(or_(AisEvent.mmsi == mmsi, AisEvent.other_mmsi == mmsi))
            .order_by(AisEvent.start_at.desc(), AisEvent.id.desc())
            .limit(50)
        )
    ).all()
    names = {mmsi: vessel.name} if vessel and vessel.name else {}
    sightings = (
        await db.execute(
            select(SarDetection, SarScene.platform, SarScene.datatake, SarScene.zone)
            .join(SarScene, SarScene.id == SarDetection.scene_id)
            .where(or_(SarDetection.mmsi == mmsi, SarDetection.suspected_mmsi == mmsi))
            .order_by(SarDetection.seen_at.desc(), SarDetection.id.desc())
            .limit(50)
        )
    ).all()

    month = datetime.now(UTC) - timedelta(days=30)
    stops = (
        await db.execute(
            select(VesselStop, Port.name, Port.country)
            .outerjoin(Port, Port.id == VesselStop.port_id)
            .where(VesselStop.mmsi == mmsi, VesselStop.end_at >= month)
            .order_by(VesselStop.start_at.desc())
            .limit(50)
        )
    ).all()
    transits = (
        await db.scalars(
            select(StraitTransit)
            .where(StraitTransit.mmsi == mmsi, StraitTransit.at >= month)
            .order_by(StraitTransit.at.desc())
            .limit(50)
        )
    ).all()

    return VesselDetail(
        mmsi=mmsi,
        name=vessel.name if vessel else None,
        callsign=vessel.callsign if vessel else None,
        imo=vessel.imo if vessel else None,
        ship_type=ship_type(vessel.ship_type) if vessel else None,
        flag=flag(mmsi),
        length_m=vessel.length_m if vessel else None,
        beam_m=vessel.beam_m if vessel else None,
        destination=vessel.destination if vessel else None,
        sanction=sanction,
        risk=risk,
        watchlists=watchlists,
        watched=watched,
        companies=companies,
        last_position=last_position,
        track=Geometry(type="LineString", coordinates=[[round(f.lon, 5), round(f.lat, 5)] for f in fixes])
        if len(fixes) > 1
        else None,
        events=[_event(e, names) for e in events],
        radar_sightings=[
            RadarSighting(
                pass_id=pass_id(platform, datatake, zone),
                seen_at=d.seen_at,
                lat=d.lat,
                lon=d.lon,
                as_suspect=d.mmsi != mmsi,
                distance_m=d.distance_m if d.mmsi == mmsi else d.suspect_distance_m,
            )
            for d, platform, datatake, zone in sightings
        ],
        stops=[
            StopOut(
                kind=s.kind,
                start_at=s.start_at,
                end_at=s.end_at,
                lat=s.lat,
                lon=s.lon,
                place=place,
                place_country=country,
                place_km=s.port_km,
            )
            for s, place, country in stops
        ],
        transits=[TransitOut(at=t.at, direction=t.direction) for t in transits],
    )


def _event(e: AisEvent, names: dict[int, str]) -> EventProperties:
    title, message, _ = describe(
        Event(e.kind, e.severity, e.mmsi, e.start_at, e.lat, e.lon, e.details, e.end_at, e.other_mmsi), names
    )
    return EventProperties(
        id=e.id,
        kind=e.kind,
        severity=e.severity,
        mmsi=e.mmsi,
        vessel_name=names.get(e.mmsi),
        other_mmsi=e.other_mmsi,
        start_at=e.start_at,
        end_at=e.end_at,
        title=title,
        message=message,
        details=e.details,
    )
