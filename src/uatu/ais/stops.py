"""Where vessels stop and when they cross the Strait. Pure functions: tracks in, stops and transits out.

A stop is a vessel staying put for an hour or more: at a berth in a port, at anchor, or lying
still at sea, where it drifts with the current. With free AIS a vessel at anchor in the Bay of
Algeciras may be heard once an hour, so a stop survives silences of a few hours as long as the
vessel is found where it was, or where drifting could have taken it. What
kind of stop it is comes first from the vessel itself (AIS navigational status "at anchor" or
"moored"), else from where it is: by the coast near a port, a berth; within 10 km of the coast,
an anchorage; further out, lying still at sea.

A transit is a crossing of the Strait's narrows, east of Tarifa: east into the Mediterranean or
west into the Atlantic. The meridian is east of Tarifa's and Tangier's ferry terminals and west
of Algeciras and Tanger Med, so that the ferries crossing between them never count.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from uatu.ais.events import KNOT, CoastFn, LocalProjection, Track

AT_ANCHOR, MOORED = 1, 5  # AIS navigational status


@dataclass(frozen=True)
class StopParams:
    max_sog: float = 1.0  # knots: slower than this, stopped (a vessel swinging at anchor shows 0-1 kn)
    under_way_sog: float = 3.0  # a report at this speed or more ends a stop
    # Between two reports, a stopped vessel moves no faster than this: drifting, or swinging at anchor
    # (a few hundred metres, which close reports can make look fast: below jitter_m it never counts).
    drift_sog: float = 2.0
    jitter_m: float = 500
    max_silence_s: float = 6 * 3600  # still the same stop after this long unheard, if found in place
    min_s: float = 3600
    min_reports: int = 2
    berth_coast_m: float = 1_000  # closer to the coast than this, and by a port: alongside
    berth_port_m: float = 5_000  # the World Port Index places a port to the nearest minute (~2 km)
    anchorage_coast_m: float = 10_000  # within this of the coast: an anchorage; beyond, lying still at sea
    place_m: float = 30_000  # a stop is named after the nearest port within this
    strait_lon: float = -5.55
    strait_lats: tuple[float, float] = (35.85, 36.06)  # the narrows between the two coasts on that meridian
    transit_max_gap_s: float = 3 * 3600  # two reports further apart don't say where it crossed


DEFAULT_STOPS = StopParams()


@dataclass(frozen=True)
class Port:
    id: int  # World Port Index number
    name: str
    country: str
    lon: float
    lat: float


@dataclass(frozen=True)
class Stop:
    mmsi: int
    kind: str  # berth | anchorage | drifting
    start: datetime
    end: datetime
    lat: float
    lon: float
    coast_m: float  # NaN when unknown
    place: Port | None  # the nearest port, if within place_m
    place_m: float | None

    @property
    def hours(self) -> float:
        return round((self.end - self.start).total_seconds() / 3600, 1)


@dataclass(frozen=True)
class Transit:
    mmsi: int
    at: datetime
    direction: str  # east | west
    lat: float


class Ports:
    """Ports to name stops after, searchable by distance."""

    def __init__(self, ports: Sequence[Port]) -> None:
        self.ports = list(ports)
        self.lon = np.array([p.lon for p in self.ports], dtype=float)
        self.lat = np.array([p.lat for p in self.ports], dtype=float)

    def nearest(self, lon: float, lat: float) -> tuple[Port | None, float | None]:
        if not self.ports:
            return None, None
        kx = 111_320.0 * np.cos(np.radians(lat))
        d = np.hypot((self.lon - lon) * kx, (self.lat - lat) * 110_540.0)
        k = int(np.argmin(d))
        return self.ports[k], float(d[k])


def detect_stops(
    tracks: Sequence[Track], coast: CoastFn, ports: Ports, proj: LocalProjection, p: StopParams = DEFAULT_STOPS
) -> list[Stop]:
    stops = []
    for tr in tracks:
        x, y = proj.xy(tr.lon, tr.lat)
        i, n = 0, len(tr.t)
        while i < n:
            if not tr.sog[i] <= p.max_sog:  # moving, or speed unknown
                i += 1
                continue
            members = [i]
            j = i + 1
            while j < n:
                last = members[-1]
                dt = tr.t[j] - tr.t[last]
                moved = float(np.hypot(x[j] - x[last], y[j] - y[last]))
                if dt > p.max_silence_s or tr.sog[j] >= p.under_way_sog:
                    break
                if moved > p.jitter_m and moved > p.drift_sog * KNOT * dt:  # it went somewhere meanwhile
                    break
                members.append(j)
                j += 1
            if tr.t[members[-1]] - tr.t[members[0]] >= p.min_s and len(members) >= p.min_reports:
                stops.append(_stop(tr, members, coast, ports, p))
                i = j
            else:
                i += 1
    return stops


def _stop(tr: Track, members: list[int], coast: CoastFn, ports: Ports, p: StopParams) -> Stop:
    lon, lat = float(np.median(tr.lon[members])), float(np.median(tr.lat[members]))
    coast_m = float(coast(np.array([lon]), np.array([lat]))[0])
    port, port_m = ports.nearest(lon, lat)
    nav = tr.nav[members] if tr.nav is not None else np.empty(0)
    known = nav[~np.isnan(nav)]
    moored = (known == MOORED).sum() * 2 > len(known) > 0
    anchored = (known == AT_ANCHOR).sum() * 2 > len(known) > 0
    by_port = port_m is not None and port_m <= p.berth_port_m
    if moored or (not anchored and coast_m < p.berth_coast_m and by_port):
        kind = "berth"
    elif (
        anchored or coast_m < p.anchorage_coast_m or (np.isnan(coast_m) and port_m is not None and port_m <= p.place_m)
    ):
        kind = "anchorage"
    else:
        kind = "drifting"
    named = port_m is not None and port_m <= p.place_m
    return Stop(
        mmsi=tr.mmsi,
        kind=kind,
        start=_utc(tr.t[members[0]]),
        end=_utc(tr.t[members[-1]]),
        lat=lat,
        lon=lon,
        coast_m=coast_m,
        place=port if named else None,
        place_m=port_m if named else None,
    )


def detect_transits(tracks: Sequence[Track], p: StopParams = DEFAULT_STOPS) -> list[Transit]:
    """Crossings of the Strait's narrows between two reports, placed by linear interpolation."""
    transits = []
    for tr in tracks:
        side = np.sign(tr.lon - p.strait_lon)
        for k in np.where(side[:-1] * side[1:] < 0)[0]:
            if tr.t[k + 1] - tr.t[k] > p.transit_max_gap_s:
                continue
            f = (p.strait_lon - tr.lon[k]) / (tr.lon[k + 1] - tr.lon[k])
            lat = float(tr.lat[k] + f * (tr.lat[k + 1] - tr.lat[k]))
            if not p.strait_lats[0] <= lat <= p.strait_lats[1]:
                continue
            at = _utc(tr.t[k] + f * (tr.t[k + 1] - tr.t[k]))
            transits.append(Transit(tr.mmsi, at, "east" if tr.lon[k + 1] > tr.lon[k] else "west", round(lat, 5)))
    return transits


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(float(t), UTC)
