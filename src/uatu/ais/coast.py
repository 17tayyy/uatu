"""Distance to the nearest coast (and, on land, to the nearest water), from the SAR land mask at 100 m.

Whether something happens in a harbour or anchorage, or out at sea, changes everything about
it: two ships stopped side by side are routine bunkering off Algeciras, and something else
20 km offshore.
"""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform
from scipy import ndimage

from uatu.sar.raster import Grid


class CoastDistance:
    def __init__(self, grid: Grid, land_path: Path, cache_dir: Path, factor: int = 10) -> None:
        self.grid = grid.coarsened(factor)
        self.land_path, self.cache_dir = land_path, cache_dir
        self._inland: np.ndarray | None = None
        path = cache_dir / f"coast_distance_{self.grid.key}.tif"
        if path.exists():
            with rasterio.open(path) as src:
                self.metres = src.read(1)
            return
        land = self._land_share() > 0
        self.metres = ndimage.distance_transform_edt(~land, sampling=self.grid.resolution).astype(np.float32)
        self._write(path, self.metres)

    def at(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Metres to the nearest land for each point; NaN outside the grid."""
        return self._sample(self.metres, lons, lats)

    def inland_at(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Metres inland, to the nearest water, for each point (0 at sea); NaN outside the grid.

        A block is land if mostly land (see _land_share): harbours, marinas and wide rivers stay water.
        """
        if self._inland is None:
            path = self.cache_dir / f"inland_distance_{self.grid.key}.tif"
            if path.exists():
                with rasterio.open(path) as src:
                    self._inland = src.read(1)
            else:
                land = self._land_share() >= 0.5
                self._inland = ndimage.distance_transform_edt(land, sampling=self.grid.resolution).astype(np.float32)
                self._write(path, self._inland)
        return self._sample(self._inland, lons, lats)

    def _land_share(self) -> np.ndarray:
        """Land in each block, from the 0/1 mask: GDAL averages in the mask's own integer type, so this
        comes rounded, 1 where half or more of the block is land and 0 elsewhere."""
        with rasterio.open(self.land_path) as src:
            return src.read(1, out_shape=self.grid.shape, out_dtype="float32", resampling=Resampling.average)

    def _write(self, path: Path, values: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", **self.grid.profile(dtype="float32")) as dst:
            dst.write(values, 1)

    def _sample(self, values: np.ndarray, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        lons, lats = np.atleast_1d(lons), np.atleast_1d(lats)
        if lons.size == 0:
            return np.empty(0, dtype=np.float32)
        xs, ys = transform("EPSG:4326", self.grid.crs, list(lons), list(lats))
        cols, rows = ~self.grid.transform @ (np.asarray(xs), np.asarray(ys))
        rows, cols = np.floor(rows).astype(int), np.floor(cols).astype(int)
        inside = (rows >= 0) & (rows < self.grid.height) & (cols >= 0) & (cols < self.grid.width)
        out = np.full(lons.shape, np.nan, dtype=np.float32)
        out[inside] = values[rows[inside], cols[inside]]
        return out
