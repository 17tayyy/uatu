"""The map's own base layers: land outlines, and maritime infrastructure.

No third-party tiles: the dashboard draws only what Uatu knows, and works without internet access
beyond the map library itself. Land inside the watched area comes from the SAR land mask (ESA
WorldCover, 10 m); around it, for context, from Natural Earth's 10 m land (public domain), cut to
Iberia and northern Morocco and simplified to ~200 m (natural_earth_land.geojson).
"""

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import transform_geom
from shapely import unary_union
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox, bbox_polygon
from uatu.db.models import Infrastructure
from uatu.sar.raster import Grid
from uatu.web.basemap.schemas import InfrastructureProperties, LandProperties, MapArea, SatelliteLayer
from uatu.web.schemas import Feature, FeatureCollection, Geometry
from uatu.zones import NATURAL_EARTH_LAND


def satellite(maptiler_key: str | None) -> SatelliteLayer:
    """MapTiler's satellite imagery (up to ~0.5 m) with a key; else Sentinel-2 cloudless (10 m) by EOX."""
    if maptiler_key:
        return SatelliteLayer(
            tiles=[f"https://api.maptiler.com/tiles/satellite-v2/{{z}}/{{x}}/{{y}}.jpg?key={maptiler_key}"],
            tile_size=512,
            max_zoom=20,
            attribution='<a href="https://www.maptiler.com/copyright/" target="_blank" rel="noopener noreferrer">'
            "&copy; MapTiler</a>",
        )
    return SatelliteLayer(
        tiles=["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg"],
        tile_size=256,
        max_zoom=15,
        attribution='<a href="https://s2maps.eu" target="_blank" rel="noopener noreferrer">Sentinel-2 cloudless</a>'
        " by EOX IT Services GmbH (contiene datos modificados de Copernicus Sentinel 2025)",
    )


def area(bbox: BBox) -> MapArea:
    lat_min, lon_min, lat_max, lon_max = bbox
    return MapArea(west=lon_min, south=lat_min, east=lon_max, north=lat_max)


def land(masks: list[tuple[Grid, Path, BBox]]) -> FeatureCollection[LandProperties]:
    """Land for the map: each zone's land mask within its box, Natural Earth everywhere else."""
    features = []
    for grid, path, bbox in masks:
        box = bbox_polygon(bbox)
        for f in land_outlines(grid, path).features:
            clipped = shape(f.geometry.model_dump()).intersection(box)
            if not clipped.is_empty:
                features.append(
                    Feature(geometry=Geometry.model_validate(_rounded(mapping(clipped))), properties=LandProperties())
                )
    covered = unary_union([bbox_polygon(bbox) for _, _, bbox in masks]) if masks else None
    return FeatureCollection(features=features + context_land(covered).features)


def context_land(covered: BaseGeometry | None) -> FeatureCollection[LandProperties]:
    """Natural Earth land, minus the part `covered` by a better source."""
    features = []
    for f in json.loads(NATURAL_EARTH_LAND.read_text())["features"]:
        geometry = shape(f["geometry"])
        if covered is not None:
            geometry = geometry.difference(covered)
        if geometry.is_empty:
            continue
        features.append(
            Feature(geometry=Geometry.model_validate(_rounded(mapping(geometry))), properties=LandProperties())
        )
    return FeatureCollection(features=features)


def land_outlines(grid: Grid, land_path: Path, resolution_m: float = 100.0) -> FeatureCollection[LandProperties]:
    """Land polygons, in lon/lat, from the land mask at `resolution_m`, simplified to that scale."""
    coarse = grid.coarsened(round(resolution_m / grid.resolution))
    with rasterio.open(land_path) as src:
        land = src.read(1, out_shape=coarse.shape, resampling=Resampling.average) > 0
    features = []
    for geometry, value in shapes(land.astype(np.uint8), mask=land, transform=coarse.transform):
        if value != 1:
            continue
        lonlat = shape(transform_geom(coarse.crs, "EPSG:4326", geometry)).simplify(0.0004)
        if lonlat.is_empty or lonlat.area < 2e-6:  # specks of a few hectares
            continue
        geometry = Geometry.model_validate(_rounded(mapping(lonlat)))
        features.append(Feature(geometry=geometry, properties=LandProperties()))
    return FeatureCollection(features=features)


async def infrastructure(db: AsyncSession, bbox: BBox) -> FeatureCollection[InfrastructureProperties]:
    """Cables, pipelines, platforms, ports and cable landing points in the area, cut to it."""
    area = func.ST_GeogFromText(f"SRID=4326;{bbox_polygon(bbox).wkt}")
    rows = await db.execute(
        select(
            Infrastructure.kind,
            Infrastructure.name,
            Infrastructure.status,
            func.ST_AsGeoJSON(func.ST_Intersection(Infrastructure.geom, area), 5).label("geojson"),
        )
        .where(func.ST_Intersects(Infrastructure.geom, area))
        .order_by(Infrastructure.kind, Infrastructure.id)
    )
    return FeatureCollection(
        features=[
            Feature(
                geometry=Geometry.model_validate_json(r.geojson),
                properties=InfrastructureProperties(kind=r.kind, name=r.name, status=r.status),
            )
            for r in rows
        ]
    )


def _rounded(geojson: dict) -> dict:
    def walk(c):
        return [walk(x) for x in c] if isinstance(c[0], list | tuple) else [round(c[0], 5), round(c[1], 5)]

    return {"type": geojson["type"], "coordinates": walk(geojson["coordinates"])}
