from uatu.web.schemas import OutputSchema


class LandProperties(OutputSchema):
    pass


class UnimagedProperties(OutputSchema):
    imaged_by: str | None  # the pass whose radar image is the rest of the sea, if any


class InfrastructureProperties(OutputSchema):
    kind: str  # telecom_cable | pipeline | platform | port | landing_point
    name: str
    status: str | None


class SatelliteLayer(OutputSchema):
    """Optical imagery for the map's background, as raster tiles."""

    tiles: list[str]  # {z}/{x}/{y} URL templates
    tile_size: int
    max_zoom: int
    attribution: str  # HTML


class MapArea(OutputSchema):
    """The area Uatu watches, as a lon/lat box."""

    west: float
    south: float
    east: float
    north: float
