"""The latest radar image of every place: a mosaic of the recent passes, newest on top."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import RecordedZone, SarScene
from uatu.web.passes.service import TILE_PX, read_radar_tile, tile_lonlat_bounds, tile_png


async def recent_frames(
    db: AsyncSession, data_dir: Path, zones: list[str], days: float, z: int, x: int, y: int
) -> list[Path]:
    """The radar images of the passes over the tile in the last `days`, newest first."""
    west, south, east, north = tile_lonlat_bounds(z, x, y)
    tile = func.ST_MakeEnvelope(west, south, east, north, 4326)
    rows = await db.execute(
        select(SarScene.zone, SarScene.name)
        .join(RecordedZone, RecordedZone.key == SarScene.zone)
        .where(
            SarScene.status == "processed",
            SarScene.zone.in_(zones),
            SarScene.acquired_from >= RecordedZone.since,
            SarScene.acquired_from >= datetime.now(UTC) - timedelta(days=days),
            func.ST_Intersects(SarScene.footprint, func.geography(tile)),
        )
        .order_by(SarScene.acquired_from.desc(), SarScene.id)
    )
    paths = [data_dir / zone / name.removesuffix(".SAFE") / "radar.tif" for zone, name in rows]
    return [p for p in paths if p.exists()]


def mosaic_tile(frames: list[Path], z: int, x: int, y: int) -> bytes | None:
    """Each pixel from the newest frame that has data there."""
    rgb = np.zeros((3, TILE_PX, TILE_PX), dtype=np.uint8)
    alpha = np.zeros((TILE_PX, TILE_PX), dtype=np.uint8)
    for path in frames:
        read = read_radar_tile(path, z, x, y)
        if read is None:
            continue
        levels, mask = read
        fill = (alpha == 0) & (mask > 0)
        rgb[:, fill], alpha[fill] = levels[:, fill], mask[fill]
        if alpha.all():
            break
    return tile_png(rgb, alpha) if alpha.any() else None
