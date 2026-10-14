"""The map's own base layers: land outlines from the SAR land mask, and maritime infrastructure.

No third-party tiles: the dashboard draws only what Uatu knows, and works without internet access
beyond the map library itself.
"""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox, bbox_polygon
from uatu.db.models import Infrastructure
from uatu.sar.raster import Grid
from uatu.web.basemap.schemas import InfrastructureProperties, LandProperties, MapArea
from uatu.web.schemas import Feature, FeatureCollection, Geometry


def area(bbox: BBox) -> MapArea:
    lat_min, lon_min, lat_max, lon_max = bbox
    return MapArea(west=lon_min, south=lat_min, east=lon_max, north=lat_max)


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
