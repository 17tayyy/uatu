"""Spatial filters shared by the services."""

from typing import Any

from sqlalchemy import and_

from uatu.config import BBox


def inside(lat: Any, lon: Any, bbox: BBox) -> Any:
    """SQL condition: a lat/lon pair of columns falls within the box."""
    lat_min, lon_min, lat_max, lon_max = bbox
    return and_(lat.between(lat_min, lat_max), lon.between(lon_min, lon_max))
