"""Response envelopes and GeoJSON shapes shared by every domain."""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

# Measurements stored as REAL come back with float noise (14.899999618530273): send what they mean.
OneDecimal = Annotated[float, AfterValidator(lambda v: round(v, 1))]
Whole = Annotated[float, AfterValidator(lambda v: round(v))]
Degrees = Annotated[float, AfterValidator(lambda v: round(v, 6))]  # ~10 cm


class OutputSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DataResponse[T](BaseModel):
    data: T


class Geometry(BaseModel):
    type: str
    coordinates: Any


class Feature[P: BaseModel](BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: Geometry
    properties: P


class FeatureCollection[P: BaseModel](BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature[P]]


def point(lon: float, lat: float) -> Geometry:
    return Geometry(type="Point", coordinates=[round(lon, 6), round(lat, 6)])
