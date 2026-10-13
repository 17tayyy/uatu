from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import NoDecode

from uatu.config import DatabaseSettings

type BBox = tuple[float, float, float, float]


class RecorderSettings(DatabaseSettings):
    aisstream_api_key: SecretStr
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    # lat_min,lon_min,lat_max,lon_max. Default: Strait of Gibraltar.
    ais_bbox: Annotated[BBox, NoDecode] = (35.5, -6.5, 36.5, -4.5)

    batch_size: int = Field(1000, gt=0)
    flush_interval_s: float = Field(2.0, gt=0)
    queue_max: int = Field(200_000, gt=0)
    stale_timeout_s: float = Field(60.0, gt=0)
    backoff_initial_s: float = Field(1.0, gt=0)
    backoff_max_s: float = Field(60.0, gt=0)
    shutdown_timeout_s: float = Field(20.0, gt=0)
    raw_archive_dir: Path | None = None
    stats_interval_s: float = Field(60.0, gt=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("ais_bbox", mode="before")
    @classmethod
    def _parse_bbox(cls, value: object) -> object:
        if isinstance(value, str):
            value = tuple(float(part) for part in value.split(","))
        return value

    @field_validator("ais_bbox")
    @classmethod
    def _check_bbox(cls, bbox: BBox) -> BBox:
        lat_min, lon_min, lat_max, lon_max = bbox
        if not (-90 <= lat_min < lat_max <= 90 and -180 <= lon_min < lon_max <= 180):
            raise ValueError("expected lat_min,lon_min,lat_max,lon_max with min < max")
        return bbox

    @property
    def bounding_box(self) -> list[list[float]]:
        """The area in aisstream.io's [[lat, lon], [lat, lon]] format."""
        lat_min, lon_min, lat_max, lon_max = self.ais_bbox
        return [[lat_min, lon_min], [lat_max, lon_max]]
