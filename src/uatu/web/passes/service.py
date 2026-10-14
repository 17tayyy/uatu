"""Satellite passes processed while AIS was being recorded, and what each one found."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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


def _summaries() -> Select:
    def count(condition: Any) -> Any:
        return (
            select(func.count())
            .where(SarDetection.scene_id == SarScene.id, condition)
            .correlate(SarScene)
            .scalar_subquery()
        )

    recording_since = select(func.min(IngestSession.connected_at)).scalar_subquery()
    interesting = and_(
        SarDetection.status == "dark",
        not_(_AT_FIXED),
        or_(SarDetection.suspected_mmsi.is_(None), SarDetection.suspect_silent_minutes >= RECEPTION_GAP_MINUTES),
    )
    return select(
        SarScene,
        func.ST_AsGeoJSON(SarScene.footprint, 5).label("footprint_json"),
        count(True).label("n_detections"),
        count(and_(SarDetection.status == "matched", not_(_AT_FIXED))).label("n_matched"),
        count(and_(SarDetection.status == "no_coverage", not_(_AT_FIXED))).label("n_no_coverage"),
        count(_AT_FIXED).label("n_fixed"),
        count(interesting).label("n_candidates"),
    ).where(SarScene.status == "processed", SarScene.acquired_from >= recording_since)


def _overlay_dir(data_dir: Path, scene_name: str) -> Path:
    return data_dir / scene_name.removesuffix(".SAFE")


def _summary(row: Any, data_dir: Path) -> PassSummary:
    scene = row.SarScene
    return PassSummary(
        id=scene.id,
        name=scene.name,
        platform=scene.platform,
        acquired_at=scene.acquired_from,
        orbit_direction=scene.orbit_direction,
        coverage=round(scene.aoi_coverage, 2),
        detections=row.n_detections,
        matched=row.n_matched,
        no_coverage=row.n_no_coverage,
        fixed=row.n_fixed,
        candidates=row.n_candidates,
        footprint=Geometry.model_validate_json(row.footprint_json),
        has_image=(_overlay_dir(data_dir, scene.name) / "backscatter.png").exists(),
    )


async def list_passes(db: AsyncSession, data_dir: Path, limit: int) -> list[PassSummary]:
    stmt = _summaries().order_by(SarScene.acquired_from.desc(), SarScene.id.desc()).limit(limit)
    return [_summary(row, data_dir) for row in (await db.execute(stmt)).all()]


async def get_pass(db: AsyncSession, data_dir: Path, pass_id: UUID) -> PassDetail:
    row = (await db.execute(_summaries().where(SarScene.id == pass_id))).first()
    if row is None:
        raise NotFoundError("Pass")
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
            .where(SarDetection.scene_id == pass_id)
            .order_by(SarDetection.id)
        )
    ).all()
    checks = (
        await db.execute(
            select(SarAisCheck, Vessel.name)
            .outerjoin(Vessel, Vessel.mmsi == SarAisCheck.mmsi)
            .where(SarAisCheck.scene_id == pass_id, SarAisCheck.status != "not_imaged")
            .order_by(SarAisCheck.id)
        )
    ).all()

    image = None
    overlay = _overlay_dir(data_dir, summary.name)
    if summary.has_image and (overlay / "backscatter.json").exists():
        image = ImageOverlay(
            url=f"/api/v1/passes/{pass_id}/image",
            coordinates=json.loads((overlay / "backscatter.json").read_text()),
        )

    return PassDetail(
        **summary.model_dump(),
        image=image,
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


async def get_pass_image(db: AsyncSession, data_dir: Path, pass_id: UUID) -> Path:
    name = await db.scalar(select(SarScene.name).where(SarScene.id == pass_id))
    if name is None:
        raise NotFoundError("Pass")
    path = _overlay_dir(data_dir, name) / "backscatter.png"
    if not path.exists():
        raise NotFoundError("Pass image")
    return path


def _difference(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 1)
