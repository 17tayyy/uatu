from enum import StrEnum

from uatu.web.schemas import OutputSchema


class Reception(StrEnum):
    GOOD = "good"  # vessels heard many times while they cross a cell
    PATCHY = "patchy"
    POOR = "poor"  # vessels heard about once: a receiver only catches them now and then


class CellProperties(OutputSchema):
    vessels: int
    reports: int
    reports_per_vessel: float
    reception: Reception
