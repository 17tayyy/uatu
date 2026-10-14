from functools import lru_cache
from pathlib import Path

from uatu.config import STRAIT_OF_GIBRALTAR, BBoxSetting, DatabaseSettings, LogSettings


class WebSettings(DatabaseSettings, LogSettings):
    ais_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR
    sar_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR
    sar_data_dir: Path = Path("data/sar")  # per-scene outputs (radar overlay) and the land mask cache
    db_pool_size: int = 5
    db_statement_timeout_ms: int = 15_000


@lru_cache
def get_settings() -> WebSettings:
    return WebSettings()
