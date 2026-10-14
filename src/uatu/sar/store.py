"""Scenes and their results in the database."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import aliased

from uatu.db.models import (
    IngestSession,
    SanctionedVessel,
    SarAisCheck,
    SarDetection,
    SarFixedObject,
    SarScene,
    Vessel,
)
from uatu.sar.analysis import Analysis
from uatu.sar.catalog import Scene, frame_of


async def ais_recording_start(engine: AsyncEngine) -> datetime | None:
    """When the recorder first connected: scenes acquired before have no AIS to pair with."""
    async with engine.connect() as conn:
        return await conn.scalar(select(func.min(IngestSession.connected_at)))


async def queue_new(engine: AsyncEngine, scenes: list[Scene], coverage: dict[str, float]) -> list[Scene]:
    """Record the scenes not seen before as queued; return those.

    A frame can be published twice, as a standard product and later as a COG one with another id:
    a frame already recorded under either is not new.
    """
    if not scenes:
        return []
    async with engine.connect() as conn:
        names = (await conn.scalars(select(SarScene.name))).all()
    known = {frame_of(name) for name in names}
    scenes = [s for s in scenes if s.frame not in known]
    if not scenes:
        return []
    rows = [
        {
            "id": uuid.UUID(s.id),
            "name": s.name,
            "platform": s.platform,
            "datatake": s.datatake,
            "orbit_direction": s.orbit_direction,
            "relative_orbit": s.relative_orbit,
            "acquired_from": s.start,
            "acquired_to": s.end,
            "footprint": f"SRID=4326;{s.footprint.wkt}",
            "aoi_coverage": coverage[s.id],
            "status": "queued",
        }
        for s in scenes
    ]
    stmt = pg_insert(SarScene).on_conflict_do_nothing(index_elements=[SarScene.id]).returning(SarScene.id)
    async with engine.begin() as conn:
        inserted = {str(i) for i in (await conn.scalars(stmt, rows)).all()}
    return [s for s in scenes if s.id in inserted]


async def queued(engine: AsyncEngine, discovered_before: datetime | None = None) -> list[Scene]:
    stmt = select(SarScene).where(SarScene.status == "queued")
    if discovered_before is not None:
        stmt = stmt.where(SarScene.discovered_at < discovered_before)
    async with engine.connect() as conn:
        return [_scene(r) for r in (await conn.execute(stmt)).all()]


async def load(engine: AsyncEngine, scene_id: uuid.UUID) -> Scene:
    async with engine.connect() as conn:
        return _scene((await conn.execute(select(SarScene).where(SarScene.id == scene_id))).one())


async def save_results(engine: AsyncEngine, scene_id: uuid.UUID, analysis: Analysis) -> dict[str, int]:
    """Replace the scene's detections and AIS checks and mark it processed. Safe to repeat."""
    detections = analysis.output.detections
    result = analysis.result
    paired = {id(m.detection): m for m in result.matches} if result else {}
    nearby = result.ais_nearby if result else {}

    def status(d: Any) -> str:
        if result is None:
            return "unpaired"
        return "matched" if id(d) in paired else "dark" if d in result.dark else "no_coverage"

    detection_rows = [
        {
            "scene_id": scene_id,
            "seen_at": seen_at,
            "lat": d.lat,
            "lon": d.lon,
            "status": status(d),
            "mmsi": paired[id(d)].expected.vessel.mmsi if id(d) in paired else None,
            "distance_m": paired[id(d)].distance_m if id(d) in paired else None,
            "ais_vessels_nearby": nearby[d].vessels if d in nearby else None,
            "ais_reports_nearby": nearby[d].reports if d in nearby else None,
            "pixels": d.pixels,
            "length_m": d.length_m,
            "peak_db": d.peak_db,
            "vv_db": d.vv_db,
            "vh_db": _finite(d.vh_db),
            "background_db": _finite(d.background_db),
            **_suspect(analysis.suspects.get(d)),
        }
        for d, seen_at in zip(detections, analysis.seen_at(detections), strict=True)
    ]
    check_rows = []
    if result is not None:
        groups = [
            ("detected", [m.expected for m in result.matches]),
            ("undetected", result.undetected),
            ("not_imaged", result.not_imaged),
        ]
        for check_status, group in groups:
            check_rows += [
                {
                    "scene_id": scene_id,
                    "mmsi": e.vessel.mmsi,
                    "status": check_status,
                    "seen_at": e.vessel.seen_at,
                    "lat": e.vessel.lat,
                    "lon": e.vessel.lon,
                    "length_m": e.vessel.length_m,
                    "ship_type": e.vessel.ship_type,
                    "sog": e.vessel.sog,
                    "report_gap_s": e.vessel.report_gap_s,
                    "doppler_shift_m": e.shift_m,
                }
                for e in group
            ]
    summary = {
        "detections": len(detections),
        "matched": len(result.matches) if result else 0,
        "dark": len(result.dark) if result else 0,
        "no_coverage": len(result.no_coverage) if result else 0,
        "ais_vessels": analysis.ais_vessels,
        "ais_undetected": len(result.undetected) if result else 0,
    }
    async with engine.begin() as conn:
        await conn.execute(delete(SarDetection).where(SarDetection.scene_id == scene_id))
        await conn.execute(delete(SarAisCheck).where(SarAisCheck.scene_id == scene_id))
        if detection_rows:
            await conn.execute(insert(SarDetection), detection_rows)
        if check_rows:
            await conn.execute(insert(SarAisCheck), check_rows)
        await conn.execute(
            update(SarScene)
            .where(SarScene.id == scene_id)
            .values(status="processed", error=None, processed_at=datetime.now(UTC), **summary)
        )
    return summary


async def mark_failed(engine: AsyncEngine, scene_id: uuid.UUID, error: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(update(SarScene).where(SarScene.id == scene_id).values(status="failed", error=error))


def _scene(row: Any) -> Scene:
    return Scene(
        id=str(row.id),
        name=row.name,
        start=row.acquired_from,
        end=row.acquired_to,
        orbit_direction=row.orbit_direction,
        relative_orbit=row.relative_orbit,
        size_bytes=0,
        online=True,
        footprint=to_shape(row.footprint),
    )


def _suspect(suspect: Any) -> dict[str, Any]:
    if suspect is None:
        return {"suspected_mmsi": None, "suspect_distance_m": None, "suspect_silent_minutes": None}
    return {
        "suspected_mmsi": suspect.vessel.mmsi,
        "suspect_distance_m": suspect.distance_m,
        "suspect_silent_minutes": round(suspect.vessel.report_gap_s / 60, 1),
    }


def _finite(value: float) -> float | None:
    return value if value == value else None  # NaN -> NULL


# --- fixed objects ---------------------------------------------------------------------------------

# Detections of different passes within this distance are the same object. Stationary targets
# carry no Doppler shift, and geolocation agrees to tens of metres between orbits.
FIXED_EPS_M = 150.0
FIXED_MIN_PASSES = 3
FIXED_MIN_PERSISTENCE = 0.5

_RECURRING_OBJECTS = text(
    """
    WITH d AS (
        SELECT d.id, s.datatake, d.seen_at, d.length_m, ST_Transform(d.geom::geometry, CAST(:srid AS integer)) AS g
        FROM sar_detections d JOIN sar_scenes s ON s.id = d.scene_id
        WHERE s.status = 'processed'
    ), clustered AS (
        SELECT *, ST_ClusterDBSCAN(g, eps := CAST(:eps AS double precision), minpoints := 2) OVER () AS cluster FROM d
    ), objects AS (
        SELECT ST_Transform(ST_Centroid(ST_Collect(g)), 4326) AS centre,
               count(DISTINCT datatake) AS passes_seen,
               count(*) AS detections,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY length_m) AS length_m,
               min(seen_at) AS first_seen,
               max(seen_at) AS last_seen,
               array_agg(id) AS detection_ids
        FROM clustered WHERE cluster IS NOT NULL
        GROUP BY cluster
    )
    SELECT ST_X(o.centre) AS lon, ST_Y(o.centre) AS lat, o.passes_seen, o.detections, o.length_m,
           o.first_seen, o.last_seen, o.detection_ids,
           (SELECT count(DISTINCT s.datatake) FROM sar_scenes s
            WHERE s.status = 'processed' AND ST_Covers(s.footprint, o.centre::geography)) AS passes_covering
    FROM objects o
    WHERE o.passes_seen >= 2
    """
)


async def rebuild_fixed_objects(engine: AsyncEngine, srid: int) -> dict[str, int]:
    """Re-derive recurring objects from every processed pass and link detections to them.

    srid: a metric CRS for the area (its UTM zone), in which the clustering distance applies.
    """
    async with engine.begin() as conn:
        rows = (await conn.execute(_RECURRING_OBJECTS, {"srid": srid, "eps": FIXED_EPS_M})).all()
        await conn.execute(delete(SarFixedObject))  # detections' links are set to NULL
        fixed = 0
        for r in rows:
            persistence = r.passes_seen / max(r.passes_covering, r.passes_seen)
            is_fixed = r.passes_seen >= FIXED_MIN_PASSES and persistence >= FIXED_MIN_PERSISTENCE
            fixed += is_fixed
            object_id = await conn.scalar(
                insert(SarFixedObject)
                .values(
                    lat=r.lat,
                    lon=r.lon,
                    passes_seen=r.passes_seen,
                    passes_covering=max(r.passes_covering, r.passes_seen),
                    persistence=round(persistence, 3),
                    fixed=is_fixed,
                    detections=r.detections,
                    length_m=r.length_m,
                    first_seen=r.first_seen,
                    last_seen=r.last_seen,
                )
                .returning(SarFixedObject.id)
            )
            await conn.execute(
                update(SarDetection).where(SarDetection.id.in_(r.detection_ids)).values(fixed_object_id=object_id)
            )
    return {"recurring": len(rows), "fixed": fixed}


# --- scene report ----------------------------------------------------------------------------------

# A suspect silent for less than this was most likely just not received (aisstream.io hears most
# vessels only every few minutes); longer, the vessel may have switched its transponder off.
RECEPTION_GAP_MINUTES = 60.0


@dataclass(frozen=True, slots=True)
class Candidate:
    lat: float
    lon: float
    length_m: float
    seen_at: datetime
    suspected_mmsi: int | None
    suspect_name: str | None
    suspect_silent_minutes: float | None
    suspect_distance_m: float | None
    suspect_sanctioned: str | None  # sanctions programmes, if the suspect's IMO is listed

    @property
    def kind(self) -> str:
        """unidentified | went_silent | reception_gap"""
        if self.suspected_mmsi is None:
            return "unidentified"
        if (self.suspect_silent_minutes or 0) >= RECEPTION_GAP_MINUTES:
            return "went_silent"
        return "reception_gap"


@dataclass(frozen=True)
class SceneReport:
    """What a pass found: all its frames, or the ones reported on."""

    name: str  # of its first frame
    acquired: datetime
    coverage: float
    detections: int
    matched: int
    no_coverage: int
    at_fixed_objects: int
    candidates: list[Candidate]  # dark: AIS received around, none paired, not a fixed object
    frames: int = 1


async def pass_report(engine: AsyncEngine, scene_ids: list[uuid.UUID]) -> SceneReport:
    """What the given frames of a pass found, together."""
    fixed = select(SarFixedObject.id).where(SarFixedObject.fixed).scalar_subquery()
    at_fixed = SarDetection.fixed_object_id.in_(fixed)
    in_frames = SarDetection.scene_id.in_(scene_ids)
    stmt = (
        select(
            SarDetection.lat,
            SarDetection.lon,
            SarDetection.length_m,
            SarDetection.seen_at,
            SarDetection.suspected_mmsi,
            Vessel.name,
            SarDetection.suspect_silent_minutes,
            SarDetection.suspect_distance_m,
            SanctionedVessel.programs,
        )
        .outerjoin(Vessel, Vessel.mmsi == SarDetection.suspected_mmsi)
        .outerjoin(SanctionedVessel, SanctionedVessel.imo == Vessel.imo)
        .where(in_frames, SarDetection.status == "dark", ~func.coalesce(at_fixed, False))
        .order_by(SarDetection.seen_at)
    )
    counts = select(
        func.count(),
        func.count().filter(SarDetection.status == "matched"),
        func.count().filter(SarDetection.status == "no_coverage"),
        func.count().filter(func.coalesce(at_fixed, False)),
    ).where(in_frames)
    frames = select(SarScene.name, SarScene.acquired_from, SarScene.aoi_coverage).where(SarScene.id.in_(scene_ids))
    async with engine.connect() as conn:
        scenes = sorted((await conn.execute(frames)).all(), key=lambda f: f.acquired_from)
        total, matched, no_coverage, fixed_count = (await conn.execute(counts)).one()
        candidates = [Candidate(*row) for row in await conn.execute(stmt)]
    coverage = min(sum(f.aoi_coverage for f in scenes), 1.0)  # frames of a pass barely overlap
    return SceneReport(
        scenes[0].name, scenes[0].acquired_from, coverage, total, matched, no_coverage, fixed_count, candidates,
        frames=len(scenes),
    )  # fmt: skip


@dataclass(frozen=True)
class Announcement:
    """What to notify about a pass now."""

    frames: list[uuid.UUID]  # to report on
    fresh: list[uuid.UUID]  # to mark as announced
    late: bool  # frames that came in after the rest of the pass was announced


async def pass_announcement(engine: AsyncEngine, scene_id: uuid.UUID) -> Announcement | None:
    """Once a frame of a live pass is done (processed or failed): the whole pass, if no frame of it is
    still queued and none was announced; only the new frames, if the rest already was; else nothing.

    One notification per pass, not per frame: frames of a pass are found together and queued together.
    """
    this = aliased(SarScene)
    stmt = (
        select(SarScene.id, SarScene.acquired_from, SarScene.status, SarScene.notified_at)
        .join(this, and_(this.platform == SarScene.platform, this.datatake == SarScene.datatake))
        .where(this.id == scene_id)
        .order_by(SarScene.acquired_from)
    )
    async with engine.connect() as conn:
        frames = (await conn.execute(stmt)).all()
    recording_since = await ais_recording_start(engine)
    if not frames or recording_since is None or frames[0].acquired_from < recording_since:
        return None  # historical: nothing to announce
    if any(f.status == "queued" for f in frames):
        return None  # the last frame announces the pass
    fresh = [f.id for f in frames if f.status == "processed" and f.notified_at is None]
    if not fresh:
        return None
    if any(f.notified_at is not None for f in frames):
        return Announcement(fresh, fresh, late=True)
    return Announcement([f.id for f in frames if f.status == "processed"], fresh, late=False)


async def mark_notified(engine: AsyncEngine, scene_ids: list[uuid.UUID]) -> None:
    async with engine.begin() as conn:
        await conn.execute(update(SarScene).where(SarScene.id.in_(scene_ids)).values(notified_at=func.now()))


async def recent_live_passes(engine: AsyncEngine, since: datetime) -> list[list[uuid.UUID]]:
    """The frames of each pass acquired since `since` while AIS was being recorded, oldest first."""
    passes: dict[tuple[str, str], list[uuid.UUID]] = {}
    recording_since = await ais_recording_start(engine)
    if recording_since is None:
        return []
    stmt = (
        select(SarScene.platform, SarScene.datatake, SarScene.id)
        .where(SarScene.status == "processed", SarScene.acquired_from >= max(since, recording_since))
        .order_by(SarScene.acquired_from)
    )
    async with engine.connect() as conn:
        for platform, datatake, scene_id in await conn.execute(stmt):
            passes.setdefault((platform, datatake), []).append(scene_id)
    return list(passes.values())
