"""A zone's geography for the analyses: its radar grid, where land is, how far the coast."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from shapely import Polygon, unary_union
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from uatu.ais.coast import CoastDistance
from uatu.config import BBox, Zone, bbox_polygon
from uatu.sar.raster import Grid, land_cache

SAR_RESOLUTION_M = 10.0
# Natural Earth 10 m land (public domain), cut to Iberia and northern Morocco, simplified to ~200 m.
NATURAL_EARTH_LAND = Path(__file__).parent / "data" / "natural_earth_land.geojson"


@dataclass(frozen=True)
class Geography:
    zone: Zone
    area: Polygon  # lon/lat
    sea: BaseGeometry  # lon/lat: the area without its land, roughly (what a frame is worth processing for)
    grid: Grid  # the radar's, in the zone's UTM zone
    land: Path  # land mask on the grid, cached
    coast: CoastDistance


def sea_of(zone: Zone) -> BaseGeometry:
    """The zone's box without land, from Natural Earth: coarse, but enough to tell how much sea a frame covers."""
    return sea_in(zone.bbox)


@cache
def sea_in(bbox: BBox) -> BaseGeometry:
    """A box without land, from Natural Earth."""
    land = unary_union([shape(f["geometry"]) for f in json.loads(NATURAL_EARTH_LAND.read_text())["features"]])
    return bbox_polygon(bbox).difference(land)


def grid_of(zone: Zone) -> Grid:
    return Grid.covering(bbox_polygon(zone.bbox), SAR_RESOLUTION_M)


def land_path(zone: Zone, cache_dir: Path) -> Path:
    """Where the zone's land mask is cached (it may not have been built yet)."""
    return cache_dir / f"land_{grid_of(zone).key}.tif"


def geography(zone: Zone, cache_dir: Path) -> Geography:
    """Blocking: the first time, builds the land mask from ESA WorldCover (about a minute per zone)."""
    grid = grid_of(zone)
    land = land_cache(grid, cache_dir)
    return Geography(zone, bbox_polygon(zone.bbox), sea_of(zone), grid, land, CoastDistance(grid, land, cache_dir))
