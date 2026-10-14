"""Settings shared by every Uatu component, read from environment variables (or a .env file)."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from shapely import Polygon, box

type BBox = tuple[float, float, float, float]  # lat_min, lon_min, lat_max, lon_max

STRAIT_OF_GIBRALTAR: BBox = (35.5, -6.5, 36.5, -4.5)
CATALAN_COAST: BBox = (40.5, 0.5, 41.7, 2.7)  # from the Ebro delta to Barcelona and the Maresme


@dataclass(frozen=True)
class Zone:
    """An area Uatu watches."""

    key: str
    name: str  # for people, in Spanish
    bbox: BBox


ZONES: dict[str, Zone] = {
    z.key: z
    for z in [
        Zone("estrecho", "Estrecho de Gibraltar", STRAIT_OF_GIBRALTAR),
        Zone("cataluna", "Costa catalana", CATALAN_COAST),
    ]
}


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


def _parse_zones(value: object) -> object:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    return value


def _check_zones(keys: list[str]) -> list[str]:
    unknown = [k for k in keys if k not in ZONES]
    if unknown or not keys:
        raise ValueError(f"expected some of {', '.join(ZONES)}, got {', '.join(keys) or 'none'}")
    return keys


# Zones by key, written in env vars as "estrecho,cataluna".
ZonesSetting = Annotated[list[str], NoDecode, BeforeValidator(_parse_zones), AfterValidator(_check_zones)]


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
