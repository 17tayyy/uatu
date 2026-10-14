from datetime import datetime

from uatu.web.schemas import OutputSchema


class OpticalImage(OutputSchema):
    """A Sentinel-2 true colour chip around a radar detection."""

    url: str  # the JPEG
    scene: str
    platform: str  # sentinel-2a, -2b, -2c
    acquired_at: datetime
    minutes_from_radar: int  # positive: after the radar pass
    cloud: float  # share of the chip
    size_m: int  # the chip is size_m x size_m
    corners: list[list[float]]  # [lon, lat] of its top left, top right, bottom right and bottom left
