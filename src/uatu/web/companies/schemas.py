from datetime import date, datetime

from uatu.web.schemas import OutputSchema


class CompanyRole(OutputSchema):
    """A company behind a vessel, with every role the sources give it there."""

    id: str
    name: str
    kind: str  # company | person
    sanctioned: bool
    programs: str | None
    vessels: int  # it owns or manages, as far as the sources tell
    sanctioned_vessels: int  # of those
    roles: list[str]  # owner | commercial_manager | ism_manager | linked | insurer
    sources: list[str]  # OpenSanctions datasets
    since: date | None
    as_of: date | None  # the latest record (inspection, detention) it comes from


class FleetVessel(OutputSchema):
    imo: int
    name: str  # as the sources name it
    roles: list[str]
    sources: list[str]
    sanctioned: bool
    risk: str | None  # sanctioned_owner | watchlisted
    watchlists: list[str]  # shadow_fleet | war_and_sanctions | port_ban
    mmsi: int | None  # if Uatu has heard it: the MMSI it last used
    ais_name: str | None  # and the name it gives on AIS
    last_seen_at: datetime | None


class CompanyDetail(OutputSchema):
    id: str
    name: str
    kind: str
    countries: list[str]
    imos: list[str]  # IMO company numbers
    sanctioned: bool
    programs: str | None
    vessels: int
    sanctioned_vessels: int
    fleet: list[FleetVessel]  # heard by Uatu first, latest first
