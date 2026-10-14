"""Sentinel-1 GRD scenes as calibrated backscatter on a map grid, and the land mask for that grid.

Everything works window by window, so a whole scene never needs to be in memory.
"""

import hashlib
import logging
import math
import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from xml.etree import ElementTree

import numpy as np
import rasterio
from affine import Affine
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.transform import GCPTransformer
from rasterio.warp import Resampling, reproject, transform, transform_bounds
from rasterio.windows import Window
from scipy.ndimage import uniform_filter
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
    def bounds(self) -> tuple[float, float, float, float]:
        left, top = self.transform.c, self.transform.f
        return left, top - self.height * self.resolution, left + self.width * self.resolution, top

    @property
    def key(self) -> str:
        """Short fingerprint of the grid, for cache file names."""
        return hashlib.sha1(f"{self.crs.to_string()} {tuple(self.transform)} {self.shape}".encode()).hexdigest()[:10]

    def window(self, window: Window) -> Grid:
        """The part of this grid covered by a pixel window."""
        return Grid(
            self.crs,
            self.transform @ Affine.translation(window.col_off, window.row_off),
            int(window.width),
            int(window.height),
        )

    def coarsened(self, factor: int) -> Grid:
        """Same area with pixels `factor` times larger (the last row and column may stick out)."""
        return Grid(
            self.crs,
            self.transform @ Affine.scale(factor),
            math.ceil(self.width / factor),
            math.ceil(self.height / factor),
        )

    def tiles(self, size: int) -> Iterator[Window]:
        """Windows of at most size x size pixels partitioning the grid."""
        for row in range(0, self.height, size):
            for col in range(0, self.width, size):
                yield Window(col, row, min(size, self.width - col), min(size, self.height - row))

    def to_map(self, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Map coordinates (metres) of pixel centres; rows/cols may be fractional."""
        xs, ys = self.transform @ (np.asarray(cols) + 0.5, np.asarray(rows) + 0.5)
        return np.asarray(xs), np.asarray(ys)

    def to_lonlat(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lons, lats = transform(self.crs, "EPSG:4326", list(xs), list(ys))
        return np.asarray(lons), np.asarray(lats)

    def outline_lonlat(self, per_side: int = 8) -> tuple[np.ndarray, np.ndarray]:
        """Points along the grid's edges, in lon/lat."""
        left, bottom, right, top = self.bounds
        t = np.linspace(0, 1, per_side)
        xs = np.concatenate(
            [left + t * (right - left), np.full(per_side, right), right - t * (right - left), np.full(per_side, left)]
        )
        ys = np.concatenate(
            [np.full(per_side, top), top - t * (top - bottom), np.full(per_side, bottom), bottom + t * (top - bottom)]
        )
        return self.to_lonlat(xs, ys)

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


class CalibratedBand:
    """One polarisation of a zipped GRD product, read as calibrated backscatter onto any grid.

    Only the part of the product under the requested grid is read and calibrated. Pixels are
    geolocated from the product's tie points (thin plate spline), with no terrain correction:
    correct at sea level, which is all that matters for detecting ships.
    """

    def __init__(self, zip_path: Path, polarisation: str) -> None:
        measurement, calibration = _member_paths(zip_path, polarisation)
        with zipfile.ZipFile(zip_path) as z:
            self._lut = SigmaLut.from_xml(z.read(calibration))
        # Measurement rasters are stored uncompressed in the zip, so windows read directly.
        self._src = rasterio.open(f"/vsizip/{zip_path.resolve()}/{measurement}")
        self._gcps, self._gcp_crs = self._src.gcps
        self._to_pixel = GCPTransformer(self._gcps, tps=True)

    def __enter__(self) -> CalibratedBand:
        return self

    def __exit__(self, *exc: type[BaseException] | BaseException | TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._src.close()

    def warp(self, grid: Grid) -> np.ndarray:
        """Linear sigma nought on the grid; NaN where the product has no data."""
        out = np.full(grid.shape, np.nan, dtype=np.float32)
        window = self._source_window(grid)
        if window is None:
            return out
        row_off, col_off = int(window.row_off), int(window.col_off)
        dn = self._src.read(1, window=window).astype(np.float32)
        a = self._lut.at(np.arange(row_off, row_off + dn.shape[0]), np.arange(col_off, col_off + dn.shape[1]))
        calibrated = dn * dn / (a * a)
        gcps = [GroundControlPoint(g.row - row_off, g.col - col_off, g.x, g.y, g.z) for g in self._gcps]
        reproject(
            calibrated,
            out,
            gcps=gcps,
            src_crs=self._gcp_crs,
            src_nodata=0,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
            num_threads=os.cpu_count() or 1,
            SRC_METHOD="GCP_TPS",
        )
        return out

    def _source_window(self, grid: Grid, margin: int = 16) -> Window | None:
        """Product pixels under the grid, or None if they do not overlap."""
        rows, cols = self._to_pixel.rowcol(*grid.outline_lonlat(), op=float)
        row_min = max(0, math.floor(min(rows)) - margin)
        col_min = max(0, math.floor(min(cols)) - margin)
        row_max = min(self._src.height, math.ceil(max(rows)) + margin)
        col_max = min(self._src.width, math.ceil(max(cols)) + margin)
        if row_min >= row_max or col_min >= col_max:
            return None
        return Window(col_min, row_min, col_max - col_min, row_max - row_min)


def sigma0(zip_path: Path, polarisation: str, grid: Grid, tile: int = 2048) -> np.ndarray:
    """Calibrated backscatter (linear sigma nought) of one polarisation on a whole grid."""
    out = np.full(grid.shape, np.nan, dtype=np.float32)
    with CalibratedBand(zip_path, polarisation) as band:
        for w in grid.tiles(tile):
            out[w.toslices()] = band.warp(grid.window(w))
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

    def at(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """A at every (row, col) combination, as a (len(rows), len(cols)) array."""
        per_vector = np.stack([np.interp(cols, self.pixels, v) for v in self.values])  # (n, len(cols))
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


def despeckle(sigma0: np.ndarray, size: int = 5, looks: float = 4.4) -> np.ndarray:
    """Lee filter: smooth the radar's speckle where the sea or the land is uniform, keep edges and ships.

    Each pixel moves towards its neighbourhood's mean in proportion to how much of the local variation is
    speckle (a GRD product has ~4.4 looks) rather than structure. NaN (no data) is ignored and kept.
    """
    valid = np.isfinite(sigma0)
    x = np.where(valid, sigma0, 0).astype(np.float32)
    n = uniform_filter(valid.astype(np.float32), size)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = uniform_filter(x, size) / n
        variance = np.maximum(uniform_filter(x * x, size) / n - mean * mean, 0)
        ci2 = variance / (mean * mean)  # local coefficient of variation, squared
        cu2 = 1 / looks  # the same for pure speckle
        weight = np.clip((ci2 - cu2) / (ci2 * (1 + cu2)), 0, 1)
        out = mean + np.nan_to_num(weight) * (x - mean)
    return np.where(valid, out, np.nan).astype(np.float32)


SEA_RGB = np.array([8.0, 22.0, 42.0])  # what calm water shows as: dark navy


def radar_colour(vv: np.ndarray, vh: np.ndarray, gamma: float = 1.5) -> np.ndarray:
    """Radar for the eye: dark navy where the sea returns little, to white where it returns most (ships), warmer
    where the cross-polarised return (VH) is strong too, as with ships, rather than wind or waves.

    Returns 3 x height x width levels 1-255, 0 where there is no data; VV alone where VH is missing.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        vv_db, vh_db = 10 * np.log10(vv), 10 * np.log10(vh)
    strength = np.clip((vv_db + 25) / 25, 0, 1) ** gamma  # -25 dB (calm sea) .. 0 dB
    cross = np.where(np.isfinite(vh_db), np.clip((vh_db + 30) / 22, 0, 1), strength)  # -30 .. -8 dB
    white = np.stack([strength, (0.7 * strength + 0.3 * cross), 0.85 * strength])
    rgb = SEA_RGB[:, None, None] + (255 - SEA_RGB[:, None, None]) * np.clip(white, 0, 1)
    return np.where(np.isfinite(vv_db), np.clip(rgb, 1, 255), 0).astype(np.uint8)


def backscatter_grey(sigma0: np.ndarray) -> np.ndarray:
    """Linear backscatter as image levels: -25 dB (calm sea) to 0 dB (ships, land) over 1-255; 0 where there is
    no data (NaN), to be transparent."""
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(sigma0)
    grey = np.clip((db + 25) / 25 * 254 + 1, 1, 255)
    return np.where(np.isfinite(db), grey, 0).astype(np.uint8)


def land_cache(grid: Grid, cache_dir: Path, tile: int = 4096) -> Path:
    """GeoTIFF of the grid, 1 on land, from ESA WorldCover 2021 (10 m). Built once per grid."""
    path = cache_dir / f"land_{grid.key}.tif"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    lon_min, lat_min, lon_max, lat_max = transform_bounds(grid.crs, "EPSG:4326", *grid.bounds)
    worldcover = [  # tiles are 3° x 3°, named after their south-west corner
        f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
        for lat in range(math.floor(lat_min / 3) * 3, math.ceil(lat_max), 3)
        for lon in range(math.floor(lon_min / 3) * 3, math.ceil(lon_max), 3)
    ]
    with rasterio.open(partial, "w", **grid.profile(dtype="uint8", nbits=1)) as dst:
        for w in grid.tiles(tile):
            part = grid.window(w)
            land = np.zeros(part.shape, dtype=bool)
            for name in worldcover:
                classes = np.zeros(part.shape, dtype=np.uint8)
                try:
                    with rasterio.open(WORLDCOVER_URL.format(name)) as src:
                        reproject(
                            rasterio.band(src, 1),
                            classes,
                            dst_transform=part.transform,
                            dst_crs=part.crs,
                            resampling=Resampling.nearest,
                            num_threads=os.cpu_count() or 1,
                        )
                except RasterioIOError:
                    continue  # no tile: open ocean
                land |= ~np.isin(classes, _WORLDCOVER_SEA)
            dst.write(land.astype(np.uint8), 1, window=w)
    partial.replace(path)
    return path


def read_land(path: Path, window: Window) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1, window=window).astype(bool)


def land_mask(grid: Grid, cache_dir: Path) -> np.ndarray:
    """True on land, for the whole grid."""
    return read_land(land_cache(grid, cache_dir), Window(0, 0, grid.width, grid.height))
