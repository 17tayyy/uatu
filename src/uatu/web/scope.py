"""What a request looks at: one zone, or the whole coast."""

from dataclasses import dataclass

from uatu.config import ZONES, BBox, Zone

ALL = "todas"


@dataclass(frozen=True)
class Scope:
    key: str  # a zone's key, or ALL
    zones: list[Zone]  # the analysed zones it covers: radar passes, AIS events
    bbox: BBox  # where to show what was recorded: the zone, or every recorded zone for the whole coast

    @property
    def keys(self) -> list[str]:
        return [z.key for z in self.zones]


def bounding(zones: list[Zone]) -> BBox:
    """The box around the zones (for several, with open sea between them: only zones are recorded)."""
    boxes = [z.bbox for z in zones]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


WHOLE_COAST = bounding(list(ZONES.values()))  # everything the recorder records


def scope(key: str, analysed: list[Zone]) -> Scope | None:
    """The scope for a key among the analysed zones; None if it is not one of them."""
    if key == ALL:
        return Scope(ALL, analysed, WHOLE_COAST)
    match = [z for z in analysed if z.key == key]
    return Scope(key, match, match[0].bbox) if match else None
