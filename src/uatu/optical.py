"""Optical imagery of a place around a moment: Sentinel-2 to look at what the radar saw.

Sentinel-2 (10 m, true colour) passes over Spain around 11:00 UTC every two or three days; Sentinel-1
at 06:30 and 18:30. So the optical image is hours apart from the radar's at best: what it shows of a
moving ship is where it was then. Anchored ships, platforms, fish farms and moorings stay put.

Scenes come from Element 84's Earth Search catalogue of the Copernicus data, as Cloud Optimised GeoTIFFs
on AWS: a 4 km chip reads a few hundred kilobytes, without downloading the 1 GB scene.
"""

import asyncio
import io
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import numpy as np
import rasterio
import structlog
from PIL import Image, ImageDraw
from rasterio.errors import RasterioIOError
from rasterio.warp import transform
from rasterio.windows import from_bounds

log = structlog.get_logger(component="optical")

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1/search"
CLOUDY = (3, 8, 9, 10)  # scene classification: cloud shadow, medium and high probability cloud, cirrus
MAX_CLOUD = 0.3  # of the chip
MIN_DATA = 0.9  # of the chip: scenes are cut into tiles, and a point near an edge may be in another one


@dataclass(frozen=True)
class Chip:
    scene: str  # e.g. S2A_30SUF_20261014_1_L2A
    platform: str  # sentinel-2a
    acquired_at: datetime
    cloud: float  # share of the chip
    png: bytes  # true colour, contrast stretched, with a ring at the centre
    size_m: float


async def scenes_around(http: httpx.AsyncClient, lon: float, lat: float, at: datetime, days: float) -> list[dict]:
    """Sentinel-2 L2A scenes over the point within `days` of `at`, closest in time first."""
    window = f"{(at - timedelta(days=days)).isoformat()}/{(at + timedelta(days=days)).isoformat()}"
    query = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": window.replace("+00:00", "Z"),
        "limit": 50,
    }
    response = await http.post(EARTH_SEARCH, json=query)
    response.raise_for_status()
    items = response.json().get("features", [])
    return sorted(items, key=lambda f: abs(_time(f) - at))


def read_chip(item: dict, lon: float, lat: float, half_m: float) -> tuple[np.ndarray, float, float]:
    """The scene's true colour around the point (3 x h x w), and the shares of the chip that are cloud and data."""
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                      GDAL_HTTP_MAX_RETRY="2", GDAL_HTTP_TIMEOUT="20"):  # fmt: skip
        with rasterio.open(item["assets"]["visual"]["href"]) as src:
            xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
            bounds = (xs[0] - half_m, ys[0] - half_m, xs[0] + half_m, ys[0] + half_m)
            rgb = src.read(window=from_bounds(*bounds, src.transform), boundless=True, fill_value=0)
        with rasterio.open(item["assets"]["scl"]["href"]) as src:
            scl = src.read(1, window=from_bounds(*bounds, src.transform), boundless=True, fill_value=0)
    data = float((scl > 0).mean())
    cloud = float(np.isin(scl, CLOUDY).sum() / max((scl > 0).sum(), 1))
    return rgb, cloud, data


def stretch(rgb: np.ndarray, low: float = 2, high: float = 99.5, min_range: float = 60) -> np.ndarray:
    """Spread the chip's own range over the levels: open sea is nearly black in true colour. At least
    `min_range` levels are spread, so that calm water turns dark blue rather than amplified noise."""
    out = np.empty_like(rgb)
    for band in range(rgb.shape[0]):
        lo, hi = np.percentile(rgb[band], [low, high])
        scaled = np.clip((rgb[band].astype(np.float32) - lo) / max(hi - lo, min_range), 0, 1) ** 0.8
        out[band] = (scaled * 255).astype(np.uint8)
    return out


def chip_png(rgb: np.ndarray) -> bytes:
    image = Image.fromarray(np.moveaxis(rgb, 0, -1), mode="RGB")
    draw = ImageDraw.Draw(image)
    cx, cy, r = image.width / 2, image.height / 2, image.width / 20  # where the radar saw it
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(208, 59, 59), width=2)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def best_chip(
    http: httpx.AsyncClient, lon: float, lat: float, at: datetime, days: float = 3, half_m: float = 2000, tries: int = 6
) -> Chip | None:
    """The closest scene in time showing the place mostly free of cloud, as a chip; None if none in `days`."""
    for item in (await scenes_around(http, lon, lat, at, days))[:tries]:
        try:
            rgb, cloud, data = await asyncio.to_thread(read_chip, item, lon, lat, half_m)
        except RasterioIOError as e:  # a missing or unreadable asset: try the next scene
            log.warning("chip_unreadable", scene=item["id"], error=str(e))
            continue
        if data < MIN_DATA or cloud > MAX_CLOUD:
            continue
        return Chip(
            scene=item["id"],
            platform=item["properties"].get("platform", ""),
            acquired_at=_time(item),
            cloud=round(cloud, 3),
            png=await asyncio.to_thread(chip_png, stretch(rgb)),
            size_m=2 * half_m,
        )
    return None


def _time(item: dict) -> datetime:
    return datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
