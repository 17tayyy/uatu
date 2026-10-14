"""What a request looks at: one zone, or the whole coast."""

from dataclasses import dataclass

from uatu.config import BBox, Zone

ALL = "todas"


@dataclass(frozen=True)
class Scope:
    key: str  # a zone's key, or ALL
    zones: list[Zone]

    @property
    def bbox(self) -> BBox:
        """The zones' bounding box (for the whole coast, open sea between them too: only zones are recorded)."""
        boxes = [z.bbox for z in self.zones]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    @property
    def keys(self) -> list[str]:
        return [z.key for z in self.zones]


def scope(key: str, zones: list[Zone]) -> Scope | None:
    """The scope for a key among the analysed zones; None if it is not one of them."""
    if key == ALL:
        return Scope(ALL, zones)
    match = [z for z in zones if z.key == key]
    return Scope(key, match) if match else None
