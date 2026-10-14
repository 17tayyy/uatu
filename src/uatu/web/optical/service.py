"""Sentinel-2 chips of radar detections, found on first request and kept on disk."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import SarDetection
from uatu.optical import Chip, best_chip
from uatu.web.errors import NotFoundError
from uatu.web.optical.schemas import OpticalImage

RECHECK_AFTER = timedelta(hours=6)  # scenes keep arriving for a few days after a pass


async def optical(db: AsyncSession, http: httpx.AsyncClient, cache: Path, detection_id: int) -> OpticalImage | None:
    """The clearest Sentinel-2 view of the detection's place within 3 days of it; None if there is none (yet)."""
    kept, image = await asyncio.to_thread(_cached, cache, detection_id)
    if kept:
        return image
    stmt = select(SarDetection.lon, SarDetection.lat, SarDetection.seen_at).where(SarDetection.id == detection_id)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise NotFoundError("Detection")
    chip = await best_chip(http, row.lon, row.lat, row.seen_at)
    image = None if chip is None else _image(detection_id, chip, row.seen_at)
    await asyncio.to_thread(_keep, cache, detection_id, chip, image)
    return image


def optical_png(cache: Path, detection_id: int) -> Path:
    path = cache / f"{detection_id}.png"
    if not path.exists():
        raise NotFoundError("Optical image")
    return path


def _image(detection_id: int, chip: Chip, seen_at: datetime) -> OpticalImage:
    return OpticalImage(
        url=f"/api/v1/detections/{detection_id}/optical.png",
        scene=chip.scene,
        platform=chip.platform,
        acquired_at=chip.acquired_at,
        minutes_from_radar=round((chip.acquired_at - seen_at).total_seconds() / 60),
        cloud=chip.cloud,
        size_m=round(chip.size_m),
    )


def _cached(cache: Path, detection_id: int) -> tuple[bool, OpticalImage | None]:
    """(kept, image): not kept when never looked up, or when a 'none found' is old enough to look again."""
    meta = cache / f"{detection_id}.json"
    if not meta.exists():
        return False, None
    kept = json.loads(meta.read_text())
    if kept["image"]:
        return True, OpticalImage.model_validate(kept["image"])
    return datetime.now(UTC) - datetime.fromisoformat(kept["checked_at"]) < RECHECK_AFTER, None


def _keep(cache: Path, detection_id: int, chip: Chip | None, image: OpticalImage | None) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    if chip is not None:
        (cache / f"{detection_id}.png").write_bytes(chip.png)
    kept = {"checked_at": datetime.now(UTC).isoformat(), "image": image.model_dump(mode="json") if image else None}
    (cache / f"{detection_id}.json").write_text(json.dumps(kept))
