from pathlib import Path

from pydantic import SecretStr

from uatu.config import STRAIT_OF_GIBRALTAR, BBoxSetting, DatabaseSettings


class SarSettings(DatabaseSettings):
    # Copernicus Data Space Ecosystem account; only needed to download.
    cdse_username: str | None = None
    cdse_password: SecretStr | None = None

    sar_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR
    sar_data_dir: Path = Path("data/sar")
