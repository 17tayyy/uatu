"""AIS that cannot be true. Pure functions: tracks in, events out.

A transponder sends whatever it is set to send, and its GPS can be jammed or fed false positions:
- a vessel far inland: GPS spoofing or a manipulated feed (as in the Baltic and the Black Sea);
- one MMSI jumping back and forth between two places: two vessels on one identity;
- an MMSI that cannot exist (an unallocated flag, a placeholder, a coast station's) on a vessel;
- an emergency beacon (AIS-SART, man overboard, EPIRB): at sea, someone may be in trouble.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from uatu.ais import mmsi as mmsis
from uatu.ais.events import KNOT, CoastFn, Event, LocalProjection, Track


@dataclass(frozen=True)
class SpoofParams:
    inland_m: float = 2_000  # further inland than this, no ship can be (harbours and wide rivers stay water)...
    far_inland_m: float = 5_000  # ...but canals the land mask can't see go this far (Empuriabrava): beyond, spoofing
    inland_min_reports: int = 3  # one stray fix is a glitch; several, a pattern
    jump_min_m: float = 5_000
    jump_min_knots: float = 60
    shared_min_jumps: int = 3  # there and back, and there again
    at_sea_m: float = 2_000  # a beacon or a vessel further from the coast than this is at sea
    moving_sog: float = 2.0


DEFAULT_SPOOF = SpoofParams()
NOT_SHIPS = {"coast_station", "group", "invalid"}  # MMSIs no vessel should transmit positions with


def detect_on_land(tracks: Sequence[Track], inland: CoastFn, p: SpoofParams = DEFAULT_SPOOF) -> list[Event]:
    """Ships reported well inland, several times: notable far inland, informative nearer the coast."""
    events = []
    for tr in tracks:
        if mmsis.kind(tr.mmsi) not in ("ship", "craft"):
            continue
        metres = inland(tr.lon, tr.lat)
        far = np.where(metres >= p.inland_m)[0]
        if len(far) < p.inland_min_reports:
            continue
        severity = 2 if (metres[far] >= p.far_inland_m).sum() >= p.inland_min_reports else 1
        details = {"reports": len(far), "inland_km": round(float(np.nanmax(metres[far])) / 1000, 1)}
        lat, lon = float(np.median(tr.lat[far])), float(np.median(tr.lon[far]))
        start, end = _utc(tr.t[far[0]]), _utc(tr.t[far[-1]])
        events.append(Event("position_on_land", severity, tr.mmsi, start, lat, lon, details, end))
    return events


def detect_shared_mmsi(tracks: Sequence[Track], proj: LocalProjection, p: SpoofParams = DEFAULT_SPOOF) -> list[Event]:
    """One MMSI jumping back and forth between places no ship could sail between: two transmitters."""
    events = []
    for tr in tracks:
        if len(tr.t) < p.shared_min_jumps + 1:
            continue
        xs, ys = proj.xy(tr.lon, tr.lat)
        moved = np.hypot(np.diff(xs), np.diff(ys))
        knots = moved / np.maximum(np.diff(tr.t), 1) / KNOT
        jumps = np.where((moved >= p.jump_min_m) & (knots >= p.jump_min_knots))[0]
        if len(jumps) < p.shared_min_jumps:
            continue
        details = {"jumps": len(jumps), "apart_km": round(float(np.median(moved[jumps])) / 1000, 1)}
        first, last = jumps[0], jumps[-1] + 1
        events.append(
            Event(
                "shared_mmsi",
                2,
                tr.mmsi,
                _utc(tr.t[first]),
                float(tr.lat[last]),
                float(tr.lon[last]),
                details,
                _utc(tr.t[last]),
            )
        )
    return events


def detect_odd_mmsi(tracks: Sequence[Track], coast: CoastFn, p: SpoofParams = DEFAULT_SPOOF) -> list[Event]:
    """Vessels transmitting with MMSIs that cannot be a ship's, and emergency beacons.

    An odd MMSI is notable on a vessel under way at sea; a stationary one in port is likely an
    installation set up wrong. A beacon is notable at sea; in port, most likely being tested.
    """
    events = []
    for tr in tracks:
        kind = mmsis.kind(tr.mmsi)
        beacon = kind in mmsis.BEACONS.values()
        if kind not in NOT_SHIPS and not beacon:
            continue
        at_sea = bool((coast(tr.lon, tr.lat) >= p.at_sea_m).any())  # NaN (unknown): not at sea
        under_way = bool((tr.sog >= p.moving_sog).any())
        if beacon:
            event_kind, severity, details = "emergency_beacon", 2 if at_sea else 1, {"beacon": kind, "at_sea": at_sea}
        else:
            event_kind, severity = "odd_mmsi", 2 if at_sea and under_way else 1
            details = {"mmsi_kind": kind, "at_sea": at_sea, "under_way": under_way}
        events.append(
            Event(
                event_kind,
                severity,
                tr.mmsi,
                _utc(tr.t[0]),
                float(tr.lat[-1]),
                float(tr.lon[-1]),
                details,
                _utc(tr.t[-1]),
            )
        )
    return events


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(float(t), UTC)
