"""Satellite passes processed while AIS was being recorded, and what each one found.

A pass is one satellite going over the area. Its product comes in frames (consecutive ~25 s slices
of the same datatake), processed one by one but shown together: one row, one image per frame.
"""

import json
from pathlib import Path
from typing import Any

from geoalchemy2 import Geometry as GeometryType
from sqlalchemy import Select, and_, case, cast, func, not_, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by, distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox, bbox_polygon
from uatu.db.models import IngestSession, SarAisCheck, SarDetection, SarFixedObject, SarScene, Vessel
from uatu.sar.store import RECEPTION_GAP_MINUTES
from uatu.web.errors import NotFoundError
from uatu.web.passes.schemas import (
    AisCheckProperties,
    DetectionKind,
    DetectionProperties,
    ImageOverlay,
    PassDetail,
    PassSummary,
)
from uatu.web.schemas import Feature, FeatureCollection, Geometry, point

PASS_ID_PATTERN = r"^S1[A-Z]-[0-9A-F]{6}$"  # platform-datatake, e.g. S1C-013AD6
_AT_FIXED = func.coalesce(
    SarDetection.fixed_object_id.in_(select(SarFixedObject.id).where(SarFixedObject.fixed)), False
)
_KIND = case(
    (_AT_FIXED, DetectionKind.FIXED.value),
    (SarDetection.status == "matched", DetectionKind.MATCHED.value),
    (SarDetection.status == "no_coverage", DetectionKind.NO_COVERAGE.value),
    (SarDetection.status == "unpaired", DetectionKind.UNPAIRED.value),
    (SarDetection.suspected_mmsi.is_(None), DetectionKind.UNIDENTIFIED.value),
    (SarDetection.suspect_silent_minutes >= RECEPTION_GAP_MINUTES, DetectionKind.WENT_SILENT.value),
    else_=DetectionKind.RECEPTION_GAP.value,
)


def pass_id(platform: str, datatake: str) -> str:
    return f"{platform}-{datatake}"


def _detection_counts() -> Any:
    """Per frame: detections, and how many of each kind that matters for the summary."""
    worth_a_look = and_(
        SarDetection.status == "dark",
        not_(_AT_FIXED),
        or_(SarDetection.suspected_mmsi.is_(None), SarDetection.suspect_silent_minutes >= RECEPTION_GAP_MINUTES),
    )
    return (
        select(
            SarDetection.scene_id,
            func.count().label("detections"),
            func.count().filter(and_(SarDetection.status == "matched", not_(_AT_FIXED))).label("matched"),
            func.count().filter(and_(SarDetection.status == "no_coverage", not_(_AT_FIXED))).label("no_coverage"),
            func.count().filter(_AT_FIXED).label("fixed"),
            func.count().filter(worth_a_look).label("candidates"),
        )
        .group_by(SarDetection.scene_id)
        .subquery()
    )


def _summaries(bbox: BBox) -> Select:
    counts = _detection_counts()
    recording_since = select(func.min(IngestSession.connected_at)).scalar_subquery()
    area = func.ST_GeomFromText(bbox_polygon(bbox).wkt, 4326)
    footprint = func.ST_Union(cast(SarScene.footprint, GeometryType))
    return (
        select(
            SarScene.platform,
            SarScene.datatake,
            func.min(SarScene.acquired_from).label("acquired_at"),
            func.min(SarScene.orbit_direction).label("orbit_direction"),
            func.array_agg(aggregate_order_by(SarScene.id, SarScene.acquired_from)).label("frame_ids"),
            func.array_agg(aggregate_order_by(SarScene.name, SarScene.acquired_from)).label("frame_names"),
            func.ST_AsGeoJSON(footprint, 5).label("footprint_json"),
            # Like each frame's own coverage: share of the area's lon/lat polygon, frames' overlap counted once.
            (func.ST_Area(func.ST_Intersection(footprint, area)) / func.ST_Area(area)).label("coverage"),
            *(func.coalesce(func.sum(counts.c[k]), 0).label(k) for k in counts.c.keys() if k != "scene_id"),
        )
        .outerjoin(counts, counts.c.scene_id == SarScene.id)
        .where(SarScene.status == "processed", SarScene.acquired_from >= recording_since)
        .group_by(SarScene.platform, SarScene.datatake)
    )


def _overlay_dir(data_dir: Path, scene_name: str) -> Path:
    return data_dir / scene_name.removesuffix(".SAFE")


def _summary(row: Any, data_dir: Path) -> PassSummary:
    return PassSummary(
        id=pass_id(row.platform, row.datatake),
        platform=row.platform,
        acquired_at=row.acquired_at,
        orbit_direction=row.orbit_direction,
        coverage=round(min(row.coverage or 0.0, 1.0), 2),
        frames=len(row.frame_names),
        detections=row.detections,
        matched=row.matched,
        no_coverage=row.no_coverage,
        fixed=row.fixed,
        candidates=row.candidates,
        footprint=Geometry.model_validate_json(row.footprint_json),
        has_image=any((_overlay_dir(data_dir, n) / "backscatter.png").exists() for n in row.frame_names),
    )


async def list_passes(db: AsyncSession, data_dir: Path, bbox: BBox, limit: int) -> list[PassSummary]:
    stmt = (
        _summaries(bbox)
        .order_by(func.min(SarScene.acquired_from).desc(), SarScene.platform, SarScene.datatake)
        .limit(limit)
    )
    return [_summary(row, data_dir) for row in (await db.execute(stmt)).all()]


async def _pass_row(db: AsyncSession, bbox: BBox, id_: str) -> Any:
    platform, datatake = id_.split("-")
    row = (
        await db.execute(_summaries(bbox).where(SarScene.platform == platform, SarScene.datatake == datatake))
    ).first()
    if row is None:
        raise NotFoundError("Pass")
    return row


async def get_pass(db: AsyncSession, data_dir: Path, bbox: BBox, id_: str) -> PassDetail:
    row = await _pass_row(db, bbox, id_)
    summary = _summary(row, data_dir)

    paired, suspect = Vessel.__table__.alias("paired"), Vessel.__table__.alias("suspect")
    detections = (
        await db.execute(
            select(
                SarDetection,
                _KIND.label("kind"),
                paired.c.name.label("vessel_name"),
                suspect.c.name.label("suspect_name"),
                SarFixedObject.passes_seen,
            )
            .outerjoin(paired, paired.c.mmsi == SarDetection.mmsi)
            .outerjoin(suspect, suspect.c.mmsi == SarDetection.suspected_mmsi)
            .outerjoin(SarFixedObject, SarFixedObject.id == SarDetection.fixed_object_id)
            .where(SarDetection.scene_id.in_(row.frame_ids))
            .order_by(SarDetection.id)
        )
    ).all()
    # A vessel in the overlap of two frames is checked in both: keep it once, detected if either saw it.
    checks = (
        await db.execute(
            select(SarAisCheck, Vessel.name)
            .ext(distinct_on(SarAisCheck.mmsi))
            .outerjoin(Vessel, Vessel.mmsi == SarAisCheck.mmsi)
            .where(SarAisCheck.scene_id.in_(row.frame_ids), SarAisCheck.status != "not_imaged")
            .order_by(SarAisCheck.mmsi, SarAisCheck.status)
        )
    ).all()

    images = []
    for frame, name in enumerate(row.frame_names):
        png, corners = (_overlay_dir(data_dir, name) / f for f in ("backscatter.png", "backscatter.json"))
        if png.exists() and corners.exists():
            version = int(png.stat().st_mtime)  # browsers cache it: a new image, a new URL
            images.append(
                ImageOverlay(
                    url=f"/api/v1/passes/{summary.id}/images/{frame}?v={version}",
                    coordinates=json.loads(corners.read_text()),
                )
            )

    return PassDetail(
        **summary.model_dump(),
        images=images,
        detections_geojson=FeatureCollection(
            features=[
                Feature(
                    geometry=point(r.SarDetection.lon, r.SarDetection.lat),
                    properties=DetectionProperties(
                        id=r.SarDetection.id,
                        kind=r.kind,
                        seen_at=r.SarDetection.seen_at,
                        length_m=r.SarDetection.length_m,
                        contrast_db=_difference(r.SarDetection.peak_db, r.SarDetection.background_db),
                        vh_db=r.SarDetection.vh_db,
                        mmsi=r.SarDetection.mmsi,
                        vessel_name=r.vessel_name,
                        distance_m=r.SarDetection.distance_m,
                        suspected_mmsi=r.SarDetection.suspected_mmsi,
                        suspect_name=r.suspect_name,
                        suspect_silent_minutes=r.SarDetection.suspect_silent_minutes,
                        fixed_passes_seen=r.passes_seen,
                    ),
                )
                for r in detections
            ]
        ),
        ais_geojson=FeatureCollection(
            features=[
                Feature(
                    geometry=point(r.SarAisCheck.lon, r.SarAisCheck.lat),
                    properties=AisCheckProperties(
                        mmsi=r.SarAisCheck.mmsi,
                        name=r.name,
                        status=r.SarAisCheck.status,
                        length_m=r.SarAisCheck.length_m,
                        sog=r.SarAisCheck.sog,
                    ),
                )
                for r in checks
            ]
        ),
    )


async def get_pass_image(db: AsyncSession, data_dir: Path, id_: str, frame: int) -> Path:
    platform, datatake = id_.split("-")
    names = (
        await db.scalars(
            select(SarScene.name)
            .where(SarScene.platform == platform, SarScene.datatake == datatake, SarScene.status == "processed")
            .order_by(SarScene.acquired_from)
        )
    ).all()
    if frame >= len(names):
        raise NotFoundError("Pass image")
    path = _overlay_dir(data_dir, names[frame]) / "backscatter.png"
    if not path.exists():
        raise NotFoundError("Pass image")
    return path


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 1)
