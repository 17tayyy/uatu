from datetime import datetime

from uatu.web.schemas import OutputSchema


class HistoryRange(OutputSchema):
    start: datetime  # AIS has been recorded since
    end: datetime  # now


class HistoryTrack(OutputSchema):
    """One vessel's reports within a chunk of time, as parallel arrays."""

    mmsi: int
    name: str | None
    ship_type: str | None
    length_m: int | None
    flag: str | None
    sanctioned: bool
    risk: str | None  # off the sanctions lists: sanctioned_owner | watchlisted
    t: list[int]  # epoch seconds
    lon: list[float]
    lat: list[float]
    sog: list[float | None]
    cog: list[int | None]


class HistoryChunk(OutputSchema):
    start: datetime
    end: datetime
    tracks: list[HistoryTrack]


class DensityProperties(OutputSchema):
    vessel_hours: int  # hours with a given vessel in the cell, summed over vessels
    weight: float  # 0-1, on a log scale against the busiest cell
