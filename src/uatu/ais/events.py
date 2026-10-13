"""Detectors of notable behaviour in AIS tracks. Pure functions: tracks in, events out.

Thresholds follow common practice in maritime analytics (e.g. Global Fishing Watch's
encounters: two vessels within 500 m, both under 2 knots, for at least 2 hours), adjusted to
what aisstream.io delivers: about one position per vessel per minute at best.
"""

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
from scipy.spatial import cKDTree
from shapely import Geometry, STRtree
from shapely import points as shapely_points

from uatu.config import BBox

KNOT = 0.514444  # m/s
NM = 1852.0  # m


@dataclass(frozen=True)
class Track:
    """One vessel's positions, time-ordered. Times in epoch seconds; sog/cog NaN when unknown."""

    mmsi: int
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    sog: np.ndarray
    cog: np.ndarray


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    severity: int  # 1 info, 2 notable, 3 alert
    mmsi: int
    start: datetime
    lat: float
    lon: float
    details: dict = field(default_factory=dict)
    end: datetime | None = None  # None while ongoing; equal to start for instantaneous events
    other_mmsi: int | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.mmsi}:{self.other_mmsi or ''}:{int(self.start.timestamp())}"


@dataclass(frozen=True, slots=True)
class Params:
    gap_min_s: float = 3600  # silence that counts as a gap
    coverage_radius_m: float = 15_000  # ...if AIS from other vessels kept arriving around it
    coverage_min_vessels: int = 3
    edge_margin_m: float = 10_000  # a vessel silent this close to the area's edge simply left it
    in_port_m: float = 2_000  # closer to the coast than this: harbour or anchorage
    moving_sog: float = 2.0  # a vessel going silent at this speed or more was under way
    # Vessels stopped around a place make it an anchorage or waiting area. Ships waiting east of
    # Gibraltar drift kilometres apart: 8 km and 4 vessels found that area on the first day's data.
    crowd_radius_m: float = 8_000
    crowd_min_vessels: int = 4
    offshore_m: float = 5_000  # farther than this: open sea
    encounter_distance_m: float = 500
    encounter_max_sog: float = 2.0
    encounter_min_s: float = 2 * 3600
    bin_s: float = 600  # time step for encounters
    fill_s: float = 900  # a position stands for the vessel this long
    loiter_max_sog: float = 2.0
    loiter_min_s: float = 3 * 3600
    jump_min_m: float = 5_000
    jump_min_knots: float = 60
    infra_distance_m: float = 1_000
    infra_max_sog: float = 3.0
    infra_min_s: float = 1800


DEFAULT_PARAMS = Params()

type CoastFn = Callable[[np.ndarray, np.ndarray], np.ndarray]  # lon, lat -> metres to land (NaN unknown)


class LocalProjection:
    """Equirectangular metres around a point: accurate to < 1% across a strait-sized area."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0, self.lat0 = lon0, lat0
        self.kx = 111_320.0 * math.cos(math.radians(lat0))
        self.ky = 110_540.0

    def xy(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def lonlat(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.lon0 + np.asarray(x) / self.kx, self.lat0 + np.asarray(y) / self.ky


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(float(t), UTC)


def _runs(mask: np.ndarray) -> Iterable[tuple[int, int]]:
    """(first, last) index of each run of True values."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return zip(np.where(edges == 1)[0], np.where(edges == -1)[0] - 1, strict=True)


# --- AIS gaps --------------------------------------------------------------------------------------


def detect_gaps(
    tracks: Sequence[Track], now: float, bbox: BBox, coast: CoastFn, proj: LocalProjection, p: Params = DEFAULT_PARAMS
) -> list[Event]:
    """Vessels that stopped transmitting where AIS was otherwise being received.

    A silence only counts if other vessels around the last position kept being heard during
    it (so neither aisstream's coverage nor Uatu was blind there), and if, sailing on at its
    last speed and course, the vessel would not simply have left the area. Silences matter
    most for a vessel under way at sea; heading into port (where aisstream hears little),
    stopped, or with patchy reception all along, they are only informative.
    """
    all_t, all_x, all_y, all_mmsi = _all_fixes(tracks, proj)

    lat_min, lon_min, lat_max, lon_max = bbox
    edge_x0, edge_y0 = proj.xy(np.array([lon_min]), np.array([lat_min]))
    edge_x1, edge_y1 = proj.xy(np.array([lon_max]), np.array([lat_max]))

    events = []
    for tr in tracks:
        silences = [(i, tr.t[i + 1]) for i in np.where(np.diff(tr.t) >= p.gap_min_s)[0]]
        if now - tr.t[-1] >= p.gap_min_s:
            silences.append((len(tr.t) - 1, None))
        for i, resumed in silences:
            x, y = proj.xy(tr.lon[i : i + 1], tr.lat[i : i + 1])
            to_edge = min(x[0] - edge_x0[0], edge_x1[0] - x[0], y[0] - edge_y0[0], edge_y1[0] - y[0])
            if to_edge < p.edge_margin_m:
                continue
            start = tr.t[i]
            window = slice(np.searchsorted(all_t, start, "right"), np.searchsorted(all_t, start + p.gap_min_s, "right"))
            near = np.hypot(all_x[window] - x[0], all_y[window] - y[0]) <= p.coverage_radius_m
            heard = len(set(all_mmsi[window][near].tolist()) - {tr.mmsi})
            if heard < p.coverage_min_vessels:
                continue
            course = _dead_reckoning(tr, i, p.gap_min_s, proj)
            heard_ahead = None
            heading_to_port = False
            if course is not None:
                cx, cy = course
                if ((cx < edge_x0[0]) | (cx > edge_x1[0]) | (cy < edge_y0[0]) | (cy > edge_y1[0])).any():
                    continue  # it sailed out of the area
                heading_to_port = bool((coast(*proj.lonlat(cx, cy)) < p.in_port_m).any())
                # Where it should be by now: was anyone heard there? If not, it sailed into a hole
                # in aisstream's coverage (like the one west of Tarifa) rather than switching off.
                ahead = np.hypot(all_x[window] - cx[-1], all_y[window] - cy[-1]) <= p.coverage_radius_m
                heard_ahead = len(set(all_mmsi[window][ahead].tolist()) - {tr.mmsi})
            coast_m = float(coast(tr.lon[i : i + 1], tr.lat[i : i + 1])[0])
            details: dict = {
                "sog": _num(tr.sog[i]),
                "cog": _num(tr.cog[i]),
                "coast_km": _km(coast_m),
                "ais_heard_nearby": heard,
                "silent_minutes": round(((resumed or now) - start) / 60),
            }
            if resumed is not None:
                rx, ry = proj.xy(tr.lon[i + 1 : i + 2], tr.lat[i + 1 : i + 2])
                moved = float(np.hypot(rx[0] - x[0], ry[0] - y[0]))
                details |= {
                    "resumed_lat": round(float(tr.lat[i + 1]), 5),
                    "resumed_lon": round(float(tr.lon[i + 1]), 5),
                    "moved_km": round(moved / 1000, 1),
                    "average_knots": round(moved / (resumed - start) / KNOT, 1),
                }
            if heading_to_port:
                details["heading_to_port"] = True
            if heard_ahead is not None:
                details["ais_heard_ahead"] = heard_ahead
            under_way = tr.sog[i] >= p.moving_sog
            at_sea = not coast_m < p.in_port_m  # NaN (unknown) counts as at sea
            covered_ahead = heard_ahead is None or heard_ahead >= p.coverage_min_vessels
            severity = 2 if under_way and at_sea and not heading_to_port and covered_ahead else 1
            resumed_at = _utc(resumed) if resumed is not None else None
            lat, lon = float(tr.lat[i]), float(tr.lon[i])
            events.append(Event("ais_gap", severity, tr.mmsi, _utc(start), lat, lon, details, resumed_at))

    # A vessel that keeps dropping out is being received patchily, not hiding.
    per_vessel = Counter(e.mmsi for e in events)
    return [
        replace(e, severity=1, details=e.details | {"patchy_reception": True}) if per_vessel[e.mmsi] > 1 else e
        for e in events
    ]


def _dead_reckoning(tr: Track, i: int, seconds: float, proj: LocalProjection) -> tuple[np.ndarray, np.ndarray] | None:
    """Points every ~1 km along where the vessel would sail in `seconds` at its speed and course."""
    sog, cog = tr.sog[i], tr.cog[i]
    if not sog >= 1 or np.isnan(cog):
        return None
    distance = sog * KNOT * seconds
    steps = np.linspace(0, distance, int(distance // 1000) + 2)
    x, y = proj.xy(tr.lon[i : i + 1], tr.lat[i : i + 1])
    heading = math.radians(cog)
    return x[0] + steps * math.sin(heading), y[0] + steps * math.cos(heading)


def _all_fixes(tracks: Sequence[Track], proj: LocalProjection) -> tuple[np.ndarray, ...]:
    """Every position of every vessel, time-ordered: t, x, y, mmsi."""
    if not tracks:
        return np.empty(0), np.empty(0), np.empty(0), np.empty(0, dtype=np.int64)
    t = np.concatenate([tr.t for tr in tracks])
    x, y = proj.xy(np.concatenate([tr.lon for tr in tracks]), np.concatenate([tr.lat for tr in tracks]))
    mmsi = np.concatenate([np.full(len(tr.t), tr.mmsi) for tr in tracks])
    order = np.argsort(t, kind="stable")
    return t[order], x[order], y[order], mmsi[order]


# --- encounters ------------------------------------------------------------------------------------


def detect_encounters(
    tracks: Sequence[Track], start: float, end: float, coast: CoastFn, proj: LocalProjection, p: Params = DEFAULT_PARAMS
) -> list[Event]:
    """Pairs of vessels both nearly stopped within encounter_distance_m of each other for a while:
    the signature of ship-to-ship transfers (and, near ports, of routine bunkering)."""
    bins = np.arange(start + p.bin_s, end + 1, p.bin_s)
    together: dict[tuple[int, int], list[tuple[int, float, float, float]]] = defaultdict(list)
    for b, at in enumerate(bins):
        mmsis, xs, ys, lons, lats = [], [], [], [], []
        for tr in tracks:
            j = np.searchsorted(tr.t, at, "right") - 1
            if j < 0 or at - tr.t[j] > p.fill_s or not tr.sog[j] <= p.encounter_max_sog:
                continue
            mmsis.append(tr.mmsi)
            lons.append(tr.lon[j])
            lats.append(tr.lat[j])
        if len(mmsis) < 2:
            continue
        xs, ys = proj.xy(np.array(lons), np.array(lats))
        tree = cKDTree(np.column_stack([xs, ys]))
        for a, c in tree.query_pairs(p.encounter_distance_m):
            pair = (mmsis[a], mmsis[c]) if mmsis[a] < mmsis[c] else (mmsis[c], mmsis[a])
            distance = float(np.hypot(xs[a] - xs[c], ys[a] - ys[c]))
            together[pair].append((b, distance, (lons[a] + lons[c]) / 2, (lats[a] + lats[c]) / 2))

    events = []
    for (m1, m2), seen in together.items():
        bins_seen = np.zeros(len(bins), dtype=bool)
        bins_seen[[s[0] for s in seen]] = True
        bins_seen = ndimage_close(bins_seen)  # one missed step does not break an encounter
        for first, last in _runs(bins_seen):
            duration = bins[last] - bins[first]
            if duration < p.encounter_min_s:
                continue
            during = [s for s in seen if first <= s[0] <= last]
            lon = float(np.mean([s[2] for s in during]))
            lat = float(np.mean([s[3] for s in during]))
            coast_m = float(coast(np.array([lon]), np.array([lat]))[0])
            severity = 2 if coast_m >= p.offshore_m else 1
            details = {
                "hours": round(duration / 3600, 1),
                "min_distance_m": round(min(s[1] for s in during)),
                "coast_km": _km(coast_m),
                "ongoing": bool(last == len(bins) - 1),
            }
            events.append(Event("encounter", severity, m1, _utc(bins[first]), lat, lon, details, _utc(bins[last]), m2))
    return events


def ndimage_close(mask: np.ndarray) -> np.ndarray:
    """Fill single-step holes in a boolean series."""
    closed = mask.copy()
    closed[1:-1] |= mask[:-2] & mask[2:]
    return closed


# --- loitering, infrastructure ---------------------------------------------------------------------


def detect_loitering(tracks: Sequence[Track], coast: CoastFn, p: Params = DEFAULT_PARAMS) -> list[Event]:
    """Vessels nearly stopped for hours well away from the coast, outside any port or anchorage."""
    events = []
    for tr in tracks:
        coast_m = coast(tr.lon, tr.lat)
        slow_offshore = (tr.sog <= p.loiter_max_sog) & (coast_m >= p.offshore_m)
        for first, last in _stationary_runs(tr, slow_offshore, p.loiter_min_s, p.fill_s):
            details = {
                "hours": round((tr.t[last] - tr.t[first]) / 3600, 1),
                "coast_km": _km(float(np.nanmin(coast_m[first : last + 1]))),
            }
            events.append(_span_event("loitering", 2, tr, first, last, details))
    return events


def detect_infrastructure_loitering(
    tracks: Sequence[Track],
    infrastructure: Sequence[tuple[str, str, Geometry]],
    coast: CoastFn,
    proj: LocalProjection,
    p: Params = DEFAULT_PARAMS,
) -> list[Event]:
    """Vessels slow or stopped over a submarine cable or pipeline, away from ports.

    infrastructure: (kind, name, geometry in `proj` metres).
    """
    if not infrastructure:
        return []
    tree = STRtree([geom for _, _, geom in infrastructure])
    events = []
    for tr in tracks:
        slow = (tr.sog <= p.infra_max_sog) & (coast(tr.lon, tr.lat) >= p.in_port_m)
        if not slow.any():
            continue
        xs, ys = proj.xy(tr.lon, tr.lat)
        nearest = np.full(len(tr.t), -1)
        distance = np.full(len(tr.t), np.inf)
        idx = np.where(slow)[0]
        found, dist = tree.query_nearest(
            shapely_points(xs[idx], ys[idx]), max_distance=p.infra_distance_m, return_distance=True
        )
        nearest[idx[found[0]]] = found[1]
        distance[idx[found[0]]] = dist
        over = slow & (nearest >= 0)
        for first, last in _stationary_runs(tr, over, p.infra_min_s, p.fill_s):
            closest = first + int(np.argmin(distance[first : last + 1]))
            kind, name, _ = infrastructure[nearest[closest]]
            details = {
                "infrastructure": name,
                "infrastructure_kind": kind,
                "min_distance_m": round(float(distance[closest])),
                "minutes": round((tr.t[last] - tr.t[first]) / 60),
            }
            events.append(_span_event("infrastructure_loitering", 3, tr, first, last, details))
    return events


def _stationary_runs(tr: Track, mask: np.ndarray, min_s: float, fill_s: float) -> Iterable[tuple[int, int]]:
    """Runs of positions matching `mask`, with no silence longer than fill_s, lasting min_s."""
    broken = np.concatenate([[False], np.diff(tr.t) > fill_s])
    for first, last in _runs(mask):
        # Split the run wherever reports stopped for too long.
        cuts = [first, *(np.where(broken[first + 1 : last + 1])[0] + first + 1).tolist(), last + 1]
        for a, b in itertools.pairwise(cuts):
            if tr.t[b - 1] - tr.t[a] >= min_s:
                yield a, b - 1


def _span_event(kind: str, severity: int, tr: Track, first: int, last: int, details: dict) -> Event:
    return Event(
        kind,
        severity,
        tr.mmsi,
        _utc(tr.t[first]),
        float(np.mean(tr.lat[first : last + 1])),
        float(np.mean(tr.lon[first : last + 1])),
        details,
        _utc(tr.t[last]),
    )


def downgrade_in_crowds(
    events: list[Event], tracks: Sequence[Track], proj: LocalProjection, p: Params = DEFAULT_PARAMS
) -> list[Event]:
    """Lower to informative what happens where many vessels sit stopped: an anchorage or a
    waiting area, where stopping, meeting and lying over a cable are routine.

    Found from the data itself: each vessel that was stopped contributes its median stopped
    position; no anchorage charts needed.
    """
    stopped = []
    for tr in tracks:
        still = tr.sog <= 1.0
        if still.sum() >= 3:
            stopped.append((tr.mmsi, float(np.median(tr.lon[still])), float(np.median(tr.lat[still]))))
    if len(stopped) < p.crowd_min_vessels:
        return events
    xs, ys = proj.xy(np.array([s[1] for s in stopped]), np.array([s[2] for s in stopped]))
    tree = cKDTree(np.column_stack([xs, ys]))
    mmsis = [s[0] for s in stopped]
    result = []
    for e in events:
        if e.kind not in ("ais_gap", "encounter", "loitering", "infrastructure_loitering") or e.severity == 1:
            result.append(e)
            continue
        ex, ey = proj.xy(np.array([e.lon]), np.array([e.lat]))
        around = {mmsis[k] for k in tree.query_ball_point([ex[0], ey[0]], p.crowd_radius_m)} - {e.mmsi, e.other_mmsi}
        if len(around) >= p.crowd_min_vessels:
            e = replace(e, severity=1, details=e.details | {"vessels_stopped_nearby": len(around)})
        result.append(e)
    return result


# --- position jumps --------------------------------------------------------------------------------


def detect_jumps(tracks: Sequence[Track], proj: LocalProjection, p: Params = DEFAULT_PARAMS) -> list[Event]:
    """Consecutive positions further apart than any ship could sail: spoofing or a broken GPS."""
    events = []
    for tr in tracks:
        if len(tr.t) < 2:
            continue
        xs, ys = proj.xy(tr.lon, tr.lat)
        moved = np.hypot(np.diff(xs), np.diff(ys))
        knots = moved / np.maximum(np.diff(tr.t), 1) / KNOT
        for i in np.where((moved >= p.jump_min_m) & (knots >= p.jump_min_knots))[0]:
            details = {
                "jump_km": round(float(moved[i]) / 1000, 1),
                "implied_knots": round(float(knots[i])),
                "to_lat": round(float(tr.lat[i + 1]), 5),
                "to_lon": round(float(tr.lon[i + 1]), 5),
            }
            at = _utc(tr.t[i + 1])
            events.append(Event("position_jump", 1, tr.mmsi, at, float(tr.lat[i]), float(tr.lon[i]), details, at))
    return events


# --- identity --------------------------------------------------------------------------------------

IDENTITY_FIELDS = {"name": 2, "imo": 2, "callsign": 1, "ship_type": 1}  # field -> severity of a change


@dataclass(frozen=True, slots=True)
class StaticReport:
    mmsi: int
    t: datetime
    name: str | None
    imo: int | None
    callsign: str | None
    ship_type: int | None


def detect_identity_changes(
    reports: Sequence[StaticReport], since: datetime, where: dict[int, tuple[float, float]]
) -> list[Event]:
    """A vessel announcing a different name, IMO, callsign or type than before.

    reports: time-ordered, starting well before `since` so earlier values are known; only
    changes after `since` are returned. where: last known (lat, lon) per MMSI.
    """
    known: dict[tuple[int, str], object] = {}
    events = []
    for r in reports:
        for name, severity in IDENTITY_FIELDS.items():
            value = getattr(r, name)
            if value is None:
                continue
            before = known.get((r.mmsi, name))
            known[(r.mmsi, name)] = value
            if before is None or before == value or r.t < since or r.mmsi not in where:
                continue
            lat, lon = where[r.mmsi]
            details = {"field": name, "before": before, "after": value}
            events.append(Event("identity_change", severity, r.mmsi, r.t, lat, lon, details, r.t))
    return events


def ais_day(t: datetime) -> datetime:
    """Midnight UTC of t: the key of once-a-day events."""
    return datetime(t.year, t.month, t.day, tzinfo=UTC)


def _num(value: float) -> float | None:
    return None if math.isnan(value) else round(float(value), 1)


def _km(metres: float) -> float | None:
    return None if math.isnan(metres) else round(metres / 1000, 1)
