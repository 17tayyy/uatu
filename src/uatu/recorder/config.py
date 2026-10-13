from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from uatu.config import STRAIT_OF_GIBRALTAR, BBoxSetting, DatabaseSettings


class RecorderSettings(DatabaseSettings):
    aisstream_api_key: SecretStr
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    ais_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR

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

    @property
    def bounding_box(self) -> list[list[float]]:
        """The area in aisstream.io's [[lat, lon], [lat, lon]] format."""
        lat_min, lon_min, lat_max, lon_max = self.ais_bbox
        return [[lat_min, lon_min], [lat_max, lon_max]]
