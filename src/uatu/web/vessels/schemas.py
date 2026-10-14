from datetime import datetime
from uuid import UUID

from uatu.web.events.schemas import EventProperties
from uatu.web.schemas import Degrees, Feature, Geometry, OneDecimal, OutputSchema, Whole


class Sanction(OutputSchema):
    listed_as: str
    programs: str | None


class LiveVesselProperties(OutputSchema):
    mmsi: int
    name: str | None
    ship_type: str | None
    length_m: int | None
    flag: str | None
    sog: OneDecimal | None
    cog: Whole | None
    heading: int | None
    received_at: datetime
    sanctioned: bool


class VesselSummary(OutputSchema):
    mmsi: int
    name: str | None
    ship_type: str | None
    flag: str | None
    last_seen_at: datetime | None


class RadarSighting(OutputSchema):
    pass_id: UUID
    seen_at: datetime
    lat: Degrees
    lon: Degrees
    as_suspect: bool  # linked as the vessel that went silent, not paired with live AIS
    distance_m: Whole | None


class VesselDetail(OutputSchema):
    mmsi: int
    name: str | None
    callsign: str | None
    imo: int | None
    ship_type: str | None
    flag: str | None
    length_m: int | None
    beam_m: int | None
    destination: str | None
    sanction: Sanction | None
    last_position: Feature[LiveVesselProperties] | None
    track: Geometry | None  # LineString of the last hours
    events: list[EventProperties]
    radar_sightings: list[RadarSighting]
