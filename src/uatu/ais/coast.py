"""Distance to the nearest coast, from the SAR land mask at 100 m resolution.

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
        path = cache_dir / f"coast_distance_{self.grid.key}.tif"
        if path.exists():
            with rasterio.open(path) as src:
                self.metres = src.read(1)
            return
        with rasterio.open(land_path) as src:
            # Average of 0/1 over each block: any land in it makes it land.
            land = src.read(1, out_shape=self.grid.shape, out_dtype="float32", resampling=Resampling.average) > 0
        self.metres = ndimage.distance_transform_edt(~land, sampling=self.grid.resolution).astype(np.float32)
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", **self.grid.profile(dtype="float32")) as dst:
            dst.write(self.metres, 1)

    def at(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Metres to the nearest land for each point; NaN outside the grid."""
        lons, lats = np.atleast_1d(lons), np.atleast_1d(lats)
        if lons.size == 0:
            return np.empty(0, dtype=np.float32)
        xs, ys = transform("EPSG:4326", self.grid.crs, list(lons), list(lats))
        cols, rows = ~self.grid.transform @ (np.asarray(xs), np.asarray(ys))
        rows, cols = np.floor(rows).astype(int), np.floor(cols).astype(int)
        inside = (rows >= 0) & (rows < self.grid.height) & (cols >= 0) & (cols < self.grid.width)
        out = np.full(lons.shape, np.nan, dtype=np.float32)
        out[inside] = self.metres[rows[inside], cols[inside]]
        return out
