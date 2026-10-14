from datetime import datetime

from uatu.web.companies.schemas import CompanyRole
from uatu.web.events.schemas import EventProperties
from uatu.web.schemas import Degrees, Feature, Geometry, OneDecimal, OutputSchema, Whole


class Sanction(OutputSchema):
    listed_as: str
    programs: str | None


class Risk(OutputSchema):
    """Why a vessel off the sanctions lists is worth a look."""

    level: str  # sanctioned_owner | watchlisted
    reasons: list[str]  # in Spanish


class Watchlisting(OutputSchema):
    watchlist: str  # shadow_fleet | war_and_sanctions (Ukraine's GUR) | port_ban (Paris MoU)
    note: str | None  # what the source says, in its words


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
    risk: str | None  # off the sanctions lists: sanctioned_owner | watchlisted


class VesselSummary(OutputSchema):
    mmsi: int
    name: str | None
    ship_type: str | None
    flag: str | None
    last_seen_at: datetime | None


class RadarSighting(OutputSchema):
    pass_id: str  # see passes
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
    risk: Risk | None
    watchlists: list[Watchlisting]
    companies: list[CompanyRole]  # who owns and manages it, as far as the sources tell
    last_position: Feature[LiveVesselProperties] | None
    track: Geometry | None  # LineString of the last hours
    events: list[EventProperties]
    radar_sightings: list[RadarSighting]
