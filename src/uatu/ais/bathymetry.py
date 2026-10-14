"""Water depth, from the EMODnet Bathymetry digital terrain model (~115 m, CC BY 4.0).

A vessel can only harm a submarine cable or pipeline by anchoring, or dragging its anchor
across it, and anchors reach the seabed only in fairly shallow water. Where the sea is
hundreds of metres deep, as east of Gibraltar, a vessel drifting over a cable is no threat.
"""

import asyncio
from pathlib import Path

import httpx
import numpy as np
import rasterio

from uatu.config import BBox

WCS_URL = "https://ows.emodnet-bathymetry.eu/wcs"


class Bathymetry:
    def __init__(self, path: Path) -> None:
        with rasterio.open(path) as src:
            self._elevation = src.read(1)
            self._transform = src.transform
            self._nodata = src.nodata

    def depth_at(self, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
        """Metres of water at each point (positive down; negative on land); NaN outside the model."""
        lons, lats = np.atleast_1d(lons).astype(float), np.atleast_1d(lats).astype(float)
        cols, rows = ~self._transform @ (lons, lats)
        rows, cols = np.floor(rows).astype(int), np.floor(cols).astype(int)
        height, width = self._elevation.shape
        inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
        depth = np.full(lons.shape, np.nan, dtype=np.float32)
        elevation = self._elevation[rows[inside], cols[inside]].astype(np.float32)
        if self._nodata is not None:
            elevation[elevation == self._nodata] = np.nan
        depth[inside] = -elevation
        return depth


async def load_bathymetry(http: httpx.AsyncClient, bbox: BBox, cache_dir: Path) -> Bathymetry:
    """The terrain model over the area, downloaded once (a few MB) and cached."""
    lat_min, lon_min, lat_max, lon_max = bbox
    path = cache_dir / f"bathymetry_{lat_min}_{lon_min}_{lat_max}_{lon_max}.tif"
    if not path.exists():
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        params = [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("request", "GetCoverage"),
            ("coverageId", "emodnet__mean"),
            ("subset", f"Lat({lat_min},{lat_max})"),
            ("subset", f"Long({lon_min},{lon_max})"),
            ("format", "image/tiff"),
        ]
        response = await http.get(WCS_URL, params=params, timeout=httpx.Timeout(30, read=180))
        response.raise_for_status()
        partial = path.with_name(path.name + ".part")
        await asyncio.to_thread(partial.write_bytes, response.content)
        await asyncio.to_thread(partial.replace, path)
    return await asyncio.to_thread(Bathymetry, path)
