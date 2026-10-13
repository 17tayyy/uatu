"""Sentinel-1 GRD scenes as calibrated backscatter on a map grid, and the land mask for that grid."""

import hashlib
import logging
import math
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.warp import Resampling, reproject, transform, transform_bounds
from rasterio.windows import Window
from shapely import Geometry

WORLDCOVER_URL = "/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
_WORLDCOVER_SEA = (0, 80)  # no data (open sea), permanent water bodies

# rasterio hands SRC_METHOD to both the GCP transformer, which uses it, and the warper, which
# warns that it does not know it.
logging.getLogger("rasterio._env").addFilter(lambda record: "SRC_METHOD" not in record.getMessage())


@dataclass(frozen=True)
class Grid:
    """A north-up raster grid in a metric (UTM) CRS."""

    crs: CRS
    transform: Affine
    width: int
    height: int

    @classmethod
    def covering(cls, aoi: Geometry, resolution: float) -> Grid:
        """Grid over a lon/lat area, in the UTM zone of its centre, with square pixels of `resolution` metres."""
        lon, lat = aoi.centroid.x, aoi.centroid.y
        crs = CRS.from_epsg((32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1)
        left, bottom, right, top = transform_bounds("EPSG:4326", crs, *aoi.bounds)
        left = math.floor(left / resolution) * resolution
        top = math.ceil(top / resolution) * resolution
        width = math.ceil((right - left) / resolution)
        height = math.ceil((top - bottom) / resolution)
        return cls(crs, Affine(resolution, 0, left, 0, -resolution, top), width, height)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def resolution(self) -> float:
        return self.transform.a

    @property
    def key(self) -> str:
        """Short fingerprint of the grid, for cache file names."""
        return hashlib.sha1(f"{self.crs.to_string()} {tuple(self.transform)} {self.shape}".encode()).hexdigest()[:10]

    def to_map(self, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Map coordinates (metres) of pixel centres; rows/cols may be fractional."""
        xs, ys = self.transform @ (np.asarray(cols) + 0.5, np.asarray(rows) + 0.5)
        return np.asarray(xs), np.asarray(ys)

    def to_lonlat(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lons, lats = transform(self.crs, "EPSG:4326", list(xs), list(ys))
        return np.asarray(lons), np.asarray(lats)

    def profile(self, **overrides) -> dict:
        """Rasterio profile for writing a GeoTIFF on this grid."""
        return {
            "driver": "GTiff",
            "crs": self.crs,
            "transform": self.transform,
            "width": self.width,
            "height": self.height,
            "count": 1,
            "compress": "deflate",
            "tiled": True,
        } | overrides


def sigma0(zip_path: Path, polarisation: str, grid: Grid) -> np.ndarray:
    """Calibrated backscatter (linear sigma nought) of one polarisation, warped onto the grid.

    NaN where the scene has no data. Pixels are geolocated from the product's tie points
    (thin plate spline), with no terrain correction: correct at sea level, which is all
    that matters for detecting ships.
    """
    measurement, calibration = _member_paths(zip_path, polarisation)
    with zipfile.ZipFile(zip_path) as z:
        lut = SigmaLut.from_xml(z.read(calibration))
    with rasterio.open(f"/vsizip/{zip_path.resolve()}/{measurement}") as src:
        gcps, gcp_crs = src.gcps
        calibrated = np.empty(src.shape, dtype=np.float32)
        for row in range(0, src.height, 2048):
            height = min(2048, src.height - row)
            dn = src.read(1, window=Window(0, row, src.width, height)).astype(np.float32)
            a = lut.at(np.arange(row, row + height), src.width)
            calibrated[row : row + height] = dn * dn / (a * a)

    out = np.full(grid.shape, np.nan, dtype=np.float32)
    reproject(
        calibrated,
        out,
        gcps=gcps,
        src_crs=gcp_crs,
        src_nodata=0,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
        num_threads=os.cpu_count() or 1,
        warp_mem_limit=1024,
        SRC_METHOD="GCP_TPS",
    )
    return out


@dataclass(frozen=True)
class SigmaLut:
    """The sigma nought calibration look-up table of a GRD product: sigma0 = DN² / A².

    A is given on a sparse grid (a vector every few hundred lines, a value every 40 pixels)
    and interpolated bilinearly in between.
    """

    lines: np.ndarray  # (n,) line of each vector
    values: np.ndarray  # (n, m) A at `pixels` for each vector
    pixels: np.ndarray  # (m,)

    @classmethod
    def from_xml(cls, xml: bytes) -> SigmaLut:
        vectors = ElementTree.fromstring(xml).findall("calibrationVectorList/calibrationVector")
        pixels = np.array(vectors[0].findtext("pixel").split(), dtype=np.float64)
        lines = np.array([int(v.findtext("line")) for v in vectors], dtype=np.float64)
        values = np.array([v.findtext("sigmaNought").split() for v in vectors], dtype=np.float64)
        return cls(lines, values, pixels)

    def at(self, rows: np.ndarray, width: int) -> np.ndarray:
        """A for every pixel of the given rows, as a (len(rows), width) array."""
        cols = np.arange(width)
        per_vector = np.stack([np.interp(cols, self.pixels, v) for v in self.values])  # (n, width)
        upper = np.clip(np.searchsorted(self.lines, rows, side="right"), 1, len(self.lines) - 1)
        lower = upper - 1
        t = ((rows - self.lines[lower]) / (self.lines[upper] - self.lines[lower]))[:, None]
        return ((1 - t) * per_vector[lower] + t * per_vector[upper]).astype(np.float32)


def _member_paths(zip_path: Path, polarisation: str) -> tuple[str, str]:
    """Paths inside the zipped SAFE of the measurement GeoTIFF and its calibration annotation."""
    pol = f"-{polarisation.lower()}-"
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    measurement = next(n for n in names if "/measurement/" in n and pol in n and n.endswith(".tiff"))
    calibration = next(n for n in names if "/annotation/calibration/calibration-" in n and pol in n)
    return measurement, calibration


def land_mask(grid: Grid, cache_dir: Path) -> np.ndarray:
    """True on land, from ESA WorldCover 2021 (10 m). Cached on disk: it only depends on the grid."""
    path = cache_dir / f"land_{grid.key}.tif"
    if path.exists():
        with rasterio.open(path) as src:
            return src.read(1).astype(bool)

    land = np.zeros(grid.shape, dtype=bool)
    lon_min, lat_min, lon_max, lat_max = transform_bounds(grid.crs, "EPSG:4326", *_bounds(grid))
    for lat in range(math.floor(lat_min / 3) * 3, math.ceil(lat_max), 3):  # WorldCover tiles are 3° x 3°
        for lon in range(math.floor(lon_min / 3) * 3, math.ceil(lon_max), 3):
            tile = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
            classes = np.zeros(grid.shape, dtype=np.uint8)
            try:
                with rasterio.open(WORLDCOVER_URL.format(tile)) as src:
                    reproject(
                        rasterio.band(src, 1),
                        classes,
                        dst_transform=grid.transform,
                        dst_crs=grid.crs,
                        resampling=Resampling.nearest,
                        num_threads=os.cpu_count() or 1,
                    )
            except RasterioIOError:
                continue  # no tile: open ocean
            land |= ~np.isin(classes, _WORLDCOVER_SEA)

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **grid.profile(dtype="uint8", nbits=1)) as dst:
        dst.write(land.astype(np.uint8), 1)
    return land


def _bounds(grid: Grid) -> tuple[float, float, float, float]:
    left, top = grid.transform.c, grid.transform.f
    return left, top - grid.height * grid.resolution, left + grid.width * grid.resolution, top
