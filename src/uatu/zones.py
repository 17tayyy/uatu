"""A zone's geography for the analyses: its radar grid, where land is, how far the coast."""

from dataclasses import dataclass
from pathlib import Path

from shapely import Polygon

from uatu.ais.coast import CoastDistance
from uatu.config import Zone, bbox_polygon
from uatu.sar.raster import Grid, land_cache

SAR_RESOLUTION_M = 10.0


@dataclass(frozen=True)
class Geography:
    zone: Zone
    area: Polygon  # lon/lat
    grid: Grid  # the radar's, in the zone's UTM zone
    land: Path  # land mask on the grid, cached
    coast: CoastDistance


def grid_of(zone: Zone) -> Grid:
    return Grid.covering(bbox_polygon(zone.bbox), SAR_RESOLUTION_M)


def land_path(zone: Zone, cache_dir: Path) -> Path:
    """Where the zone's land mask is cached (it may not have been built yet)."""
    return cache_dir / f"land_{grid_of(zone).key}.tif"


def geography(zone: Zone, cache_dir: Path) -> Geography:
    """Blocking: the first time, builds the land mask from ESA WorldCover (about a minute per zone)."""
    grid = grid_of(zone)
    land = land_cache(grid, cache_dir)
    return Geography(zone, bbox_polygon(zone.bbox), grid, land, CoastDistance(grid, land, cache_dir))
