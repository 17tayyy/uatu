"""Ship detection over a whole scene, tile by tile, in bounded memory.

The grid is cut into tiles. Each one is processed with a halo of surrounding pixels, so the
CFAR windows, the coastal buffer and the objects themselves see the same neighbourhood as
they would in one piece; a detection belongs to the tile whose core holds its centre.
Sidelobe suppression looks across tiles, so it runs once at the end.

Besides the detections, the scene leaves coarse rasters for display and for pairing with
AIS: the backscatter, the land, and where the detector searched; and, on request, the
backscatter at 20 m, the sensor's actual resolution, despeckled and coloured from both
polarisations (see radar_colour), over the sea only (the map shows land from optical imagery),
written tile by tile to a GeoTIFF.
"""

import math
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import structlog
from rasterio.windows import Window

from uatu.sar.detect import CfarParams, Detection, candidates, sea_mask, suppress_sidelobes
from uatu.sar.raster import CalibratedBand, Grid, despeckle, radar_colour, read_land

log = structlog.get_logger(component="sar")


@dataclass(frozen=True)
class SceneOutput:
    detections: list[Detection]
    coarse: Grid  # grid of the rasters below
    vv: np.ndarray  # mean linear sigma0 (NaN outside the scene)
    land: np.ndarray  # mostly land
    searched: np.ndarray  # mostly sea the detector searched (within the scene, away from the coast)


def process(
    zip_path: Path,
    grid: Grid,
    land_path: Path,
    params: CfarParams,
    tile: int = 2048,
    coarse_factor: int = 8,
    image_path: Path | None = None,
    image_factor: int = 2,
) -> SceneOutput:
    """image_path: also write the backscatter there, `image_factor` times coarser than the grid, in colour
    (see radar_colour) on the grid's projection."""
    halo = _halo_px(params, grid.resolution)
    coarse = grid.coarsened(coarse_factor)
    fine = grid.coarsened(image_factor)
    coarse_vv = np.full(coarse.shape, np.nan, dtype=np.float32)
    coarse_land = np.zeros(coarse.shape, dtype=bool)
    coarse_searched = np.zeros(coarse.shape, dtype=bool)
    found: list[Detection] = []

    # GDAL caches 5% of the machine's RAM by default; tiles are read once, so a small cache
    # costs nothing and keeps the process at ~0.7 GB.
    with (
        rasterio.Env(GDAL_CACHEMAX=64),
        CalibratedBand(zip_path, "VV") as vv_band,
        CalibratedBand(zip_path, "VH") as vh_band,
        _image(image_path, fine) as image,
    ):
        for core in grid.tiles(tile):
            padded = _pad(core, halo, grid)
            part = grid.window(padded)
            vv = vv_band.warp(part)
            land = read_land(land_path, padded)
            inner = (
                slice(core.row_off - padded.row_off, core.row_off - padded.row_off + core.height),
                slice(core.col_off - padded.col_off, core.col_off - padded.col_off + core.width),
            )
            coarse_window = (
                slice(core.row_off // coarse_factor, math.ceil((core.row_off + core.height) / coarse_factor)),
                slice(core.col_off // coarse_factor, math.ceil((core.col_off + core.width) / coarse_factor)),
            )
            coarse_land[coarse_window] = _block_mean(land[inner], coarse_factor) > 0.5
            if not np.isfinite(vv).any():
                continue  # outside the scene

            valid = sea_mask(vv, land, part, params.coast_buffer_m)
            coarse_vv[coarse_window] = _block_mean(vv[inner], coarse_factor)
            vh = None
            if image is not None:
                vh = vh_band.warp(part)
                rgb = radar_colour(
                    _block_mean(despeckle(vv)[inner], image_factor), _block_mean(despeckle(vh)[inner], image_factor)
                )
                rgb[:, _block_mean(land[inner], image_factor) > 0.5] = 0  # land: no data, transparent
                image.write(rgb, window=Window(core.col_off // image_factor, core.row_off // image_factor,
                                               rgb.shape[2], rgb.shape[1]))  # fmt: skip
            coarse_searched[coarse_window] = _block_mean(valid[inner], coarse_factor) > 0.5
            if not valid[inner].any():
                continue  # no sea searched here

            if vh is None:
                vh = vh_band.warp(part)
            in_tile = [d for d in candidates(vv, vh, valid, part, params) if _inside(d, grid, core)]
            found += in_tile
            log.debug("tile_done", window=f"{core.row_off},{core.col_off}", detections=len(in_tile))

    detections = suppress_sidelobes(found, params.sidelobe_radius_m, params.sidelobe_contrast_db)
    return SceneOutput(detections, coarse, coarse_vv, coarse_land, coarse_searched)


def _halo_px(params: CfarParams, resolution: float) -> int:
    # Half the CFAR background window, the coastal buffer, and room for an object's extent
    # (merged hits, the largest ships) and the sea around it.
    metres = params.background_m / 2 + params.coast_buffer_m + params.merge_m + 500
    return math.ceil(metres / resolution)


def _pad(window: Window, halo: int, grid: Grid) -> Window:
    row = max(0, window.row_off - halo)
    col = max(0, window.col_off - halo)
    bottom = min(grid.height, window.row_off + window.height + halo)
    right = min(grid.width, window.col_off + window.width + halo)
    return Window(col, row, right - col, bottom - row)


def _inside(d: Detection, grid: Grid, core: Window) -> bool:
    col, row = ~grid.transform @ (d.x, d.y)
    return core.row_off <= row < core.row_off + core.height and core.col_off <= col < core.col_off + core.width


@contextmanager
def _image(path: Path | None, grid: Grid) -> Iterator[Any]:
    """A GeoTIFF on the grid to write the backscatter into, or None. Never-written blocks stay 0: no data."""
    if path is None:
        yield None
        return
    profile = grid.profile(dtype="uint8", count=3, nodata=0, blockxsize=512, blockysize=512, photometric="RGB")
    with rasterio.open(path, "w", **profile) as dst:
        yield dst


def _block_mean(a: np.ndarray, factor: int) -> np.ndarray:
    """Mean over factor x factor blocks, ignoring NaN; blocks overhanging the edge use what is there."""
    height, width = a.shape
    rows, cols = math.ceil(height / factor), math.ceil(width / factor)
    padded = np.full((rows * factor, cols * factor), np.nan, dtype=np.float32)
    padded[:height, :width] = a
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks
        return np.nanmean(padded.reshape(rows, factor, cols, factor), axis=(1, 3))
