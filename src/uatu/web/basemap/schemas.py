from uatu.web.schemas import OutputSchema


class LandProperties(OutputSchema):
    pass


class InfrastructureProperties(OutputSchema):
    kind: str  # telecom_cable | pipeline | platform | port | landing_point
    name: str
    status: str | None


class MapArea(OutputSchema):
    """The area Uatu watches, as a lon/lat box."""

    west: float
    south: float
    east: float
    north: float
