from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr

from uatu.config import DatabaseSettings, LogSettings, ZoneSettings


class WebSettings(DatabaseSettings, LogSettings, ZoneSettings):
    sar_data_dir: Path = Path("data/sar")  # per-zone scene outputs (radar overlays) and the land mask caches
    web_cache_dir: Path = Path("data/web-cache")  # what the dashboard fetches once and keeps: optical chips
    db_pool_size: int = 5
    db_statement_timeout_ms: int = 15_000
    # MapTiler (maptiler.com) for high resolution satellite imagery under the radar; without it, Sentinel-2
    # cloudless (10 m). The browser fetches the tiles: restrict the key to the dashboard's origin at MapTiler.
    maptiler_key: SecretStr | None = None


@lru_cache
def get_settings() -> WebSettings:
    return WebSettings()
