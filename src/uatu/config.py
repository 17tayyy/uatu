"""Settings shared by every Uatu component, read from environment variables (or a .env file)."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from shapely import Polygon, box

type BBox = tuple[float, float, float, float]  # lat_min, lon_min, lat_max, lon_max

STRAIT_OF_GIBRALTAR: BBox = (35.5, -6.5, 36.5, -4.5)


def _parse_bbox(value: object) -> object:
    if isinstance(value, str):
        value = tuple(float(part) for part in value.split(","))
    return value


def _check_bbox(bbox: BBox) -> BBox:
    lat_min, lon_min, lat_max, lon_max = bbox
    if not (-90 <= lat_min < lat_max <= 90 and -180 <= lon_min < lon_max <= 180):
        raise ValueError("expected lat_min,lon_min,lat_max,lon_max with min < max")
    return bbox


# A bounding box written in env vars as "lat_min,lon_min,lat_max,lon_max".
BBoxSetting = Annotated[BBox, NoDecode, BeforeValidator(_parse_bbox), AfterValidator(_check_bbox)]


def bbox_polygon(bbox: BBox) -> Polygon:
    """The bounding box as a lon/lat (x/y) polygon."""
    lat_min, lon_min, lat_max, lon_max = bbox
    return box(lon_min, lat_min, lon_max, lat_max)


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    database_url: str = "postgresql+asyncpg://uatu:uatu@localhost:5432/uatu"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, url: str) -> str:
        # Accept the plain postgresql:// URLs that psql, QGIS, etc. use.
        for scheme in ("postgresql://", "postgres://"):
            if url.startswith(scheme):
                return "postgresql+asyncpg://" + url.removeprefix(scheme)
        return url
