from pathlib import Path

from pydantic import Field, SecretStr

from uatu.config import STRAIT_OF_GIBRALTAR, BBoxSetting, DatabaseSettings, LogSettings


class SarSettings(DatabaseSettings, LogSettings):
    # Copernicus Data Space Ecosystem account; only needed to download.
    cdse_username: str | None = None
    cdse_password: SecretStr | None = None

    sar_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR
    sar_data_dir: Path = Path("data/sar")

    # Worker
    redis_url: str = "redis://localhost:6379"
    sar_discover_minutes: float = Field(10.0, gt=0)  # how often to look for new scenes
    sar_min_coverage: float = Field(0.1, ge=0, le=1)  # skip frames covering less of the area
    sar_lookback_days: float = Field(3.0, gt=0)  # how far back to look for scenes not processed yet
    sar_keep_scenes: bool = False  # keep downloaded products after processing (~1-2 GB each)
