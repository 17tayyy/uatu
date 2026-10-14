from functools import lru_cache
from pathlib import Path

from uatu.config import DatabaseSettings, LogSettings, ZoneSettings


class WebSettings(DatabaseSettings, LogSettings, ZoneSettings):
    sar_data_dir: Path = Path("data/sar")  # per-zone scene outputs (radar overlays) and the land mask caches
    db_pool_size: int = 5
    db_statement_timeout_ms: int = 15_000


@lru_cache
def get_settings() -> WebSettings:
    return WebSettings()
