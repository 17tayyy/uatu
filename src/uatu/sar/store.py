"""Scenes and their results in the database."""

import uuid
from datetime import UTC, datetime
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.db.models import IngestSession, SarAisCheck, SarDetection, SarScene
from uatu.sar.analysis import Analysis
from uatu.sar.catalog import Scene


async def ais_recording_start(engine: AsyncEngine) -> datetime | None:
    """When the recorder first connected: scenes acquired before have no AIS to pair with."""
    async with engine.connect() as conn:
        return await conn.scalar(select(func.min(IngestSession.connected_at)))


async def queue_new(engine: AsyncEngine, scenes: list[Scene], coverage: dict[str, float]) -> list[Scene]:
    """Record the scenes not seen before as queued; return those."""
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
            "ais_vessels_nearby": nearby.get(d),
            "pixels": d.pixels,
            "length_m": d.length_m,
            "peak_db": d.peak_db,
            "vv_db": d.vv_db,
            "vh_db": _finite(d.vh_db),
            "background_db": _finite(d.background_db),
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


def _finite(value: float) -> float | None:
    return value if value == value else None  # NaN -> NULL
