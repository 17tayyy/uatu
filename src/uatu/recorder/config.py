from pathlib import Path

from pydantic import Field, SecretStr

from uatu.config import ZONES, DatabaseSettings, LogSettings, ZonesSetting


class RecorderSettings(DatabaseSettings, LogSettings):
    aisstream_api_key: SecretStr
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    zones: ZonesSetting = Field(default_factory=lambda: list(ZONES))  # recorded in one subscription

    batch_size: int = Field(1000, gt=0)
    flush_interval_s: float = Field(2.0, gt=0)
    queue_max: int = Field(200_000, gt=0)
    stale_timeout_s: float = Field(60.0, gt=0)
    backoff_initial_s: float = Field(1.0, gt=0)
    backoff_max_s: float = Field(60.0, gt=0)
    shutdown_timeout_s: float = Field(20.0, gt=0)
    raw_archive_dir: Path | None = None
    stats_interval_s: float = Field(60.0, gt=0)

    @property
    def bounding_boxes(self) -> list[list[list[float]]]:
        """The zones in aisstream.io's [[[lat, lon], [lat, lon]], ...] format."""
        boxes = []
        for key in self.zones:
            lat_min, lon_min, lat_max, lon_max = ZONES[key].bbox
            boxes.append([[lat_min, lon_min], [lat_max, lon_max]])
        return boxes
