"""Pair SAR detections with AIS vessels. Detections left without a vessel are dark vessel candidates.

A moving ship does not appear where it is in a SAR image: its motion towards or away from
the radar adds a Doppler shift that the processor reads as a position along the track.
The ship is displaced along the satellite's heading by

    shift = -(R / V) * range_rate

R being the slant range, V the satellite speed (R / V ~ 110 s for Sentinel-1) and
range_rate the ship's velocity along the line of sight (positive moving away). 5 m/s of
range rate moves a ship ~550 m: AIS positions are shifted the same way before pairing.

Each AIS position also carries its own uncertainty: a ship last heard from minutes ago at
speed is somewhere around its estimated position, not exactly on it. Pairing tolerates
distances in proportion to that.

Finally, an unpaired detection only means "dark" where AIS is actually being received:
in areas where aisstream.io hears no vessels at all, it is reported as uncovered instead.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
from rasterio.warp import transform
from scipy.optimize import linear_sum_assignment

from uatu.sar.ais import AisVessel, Coverage
from uatu.sar.annotation import AcquisitionGeometry
from uatu.sar.detect import Detection
from uatu.sar.raster import Grid

KNOT = 0.514444  # m/s


@dataclass(frozen=True, slots=True)
class Expected:
    """Where an AIS vessel should appear in the image."""

    vessel: AisVessel
    x: float  # grid CRS, metres, after the Doppler shift
    y: float
    shift_m: float  # along the satellite heading
    true_xy: tuple[float, float]  # before the shift
    tolerance_m: float  # how far from (x, y) a detection may be and still be this vessel


@dataclass(frozen=True, slots=True)
class Match:
    detection: Detection
    expected: Expected
    distance_m: float


@dataclass(frozen=True)
class MatchResult:
    matches: list[Match]
    dark: list[Detection]  # detections with no AIS vessel close enough, where AIS is received
    undetected: list[Expected]  # AIS vessels in the imaged sea that the radar did not pick up
    not_imaged: list[Expected]  # AIS vessels outside the imaged sea (other frame, near the coast)
    no_coverage: list[Detection] = field(default_factory=list)  # unpaired where AIS is not received
    ais_nearby: dict[Detection, Coverage] = field(default_factory=dict)  # AIS heard around unpaired ones


def split_by_coverage(
    result: MatchResult, coverage: list[Coverage], min_vessels: int, min_reports_per_vessel: float
) -> MatchResult:
    """Move dark detections where AIS is not really being received to `no_coverage`: too few
    vessels heard around them, or each heard only now and then.

    coverage: for each detection in result.dark, the AIS received around it.
    """
    around = dict(zip(result.dark, coverage, strict=True))

    def covered(d: Detection) -> bool:
        c = around[d]
        return c.vessels >= min_vessels and c.reports_per_vessel >= min_reports_per_vessel

    return replace(
        result,
        dark=[d for d in result.dark if covered(d)],
        no_coverage=[d for d in result.dark if not covered(d)],
        ais_nearby=around,
    )


def expected_positions(
    vessels: list[AisVessel],
    geometry: AcquisitionGeometry,
    grid: Grid,
    base_tolerance_m: float = 1000.0,
    drift_fraction: float = 0.25,
    doppler_sign: float = 1.0,
) -> list[Expected]:
    """Where each vessel should appear in the image, and how far from it a detection may be.

    The tolerance grows with the distance the vessel travels between its nearest AIS report
    and the acquisition (speed x report gap): drift_fraction of it allows for turns and
    speed changes the interpolation cannot see.
    """
    if not vessels:
        return []
    lons = np.array([v.lon for v in vessels])
    lats = np.array([v.lat for v in vessels])
    xs, ys = (np.asarray(a) for a in transform("EPSG:4326", grid.crs, list(lons), list(lats)))
    _, slant_range, incidence = geometry.at(lons, lats)
    heading = math.radians(geometry.heading_deg)

    expected = []
    for i, v in enumerate(vessels):
        shift = 0.0
        speed = v.max_sog if v.max_sog is not None else v.sog
        tolerance = base_tolerance_m + drift_fraction * (speed or 0.0) * KNOT * v.report_gap_s
        if v.sog is not None and v.cog is not None:
            ground_range_rate = v.sog * KNOT * math.cos(math.radians(v.cog - geometry.look_deg))
            range_rate = ground_range_rate * math.sin(math.radians(incidence[i]))
            shift = -doppler_sign * slant_range[i] / geometry.satellite_speed * range_rate
        x = xs[i] + shift * math.sin(heading)
        y = ys[i] + shift * math.cos(heading)
        expected.append(
            Expected(v, float(x), float(y), round(shift, 1), (float(xs[i]), float(ys[i])), round(tolerance, 1))
        )
    return expected


def match(
    detections: list[Detection], expected: list[Expected], imaged: np.ndarray, grid: Grid, edge_margin_m: float = 1000.0
) -> MatchResult:
    """Pair detections with expected AIS positions one-to-one (Hungarian method), minimising the
    total of distances measured in units of each vessel's tolerance, and only within it.

    `imaged` marks the sea the detector actually searched. AIS vessels outside it could not
    have been detected and are reported apart, except that those within edge_margin_m of it
    may still pair: their AIS position can be slightly off where the radar did see them.
    """
    inv = ~grid.transform
    margin = round(edge_margin_m / grid.resolution)

    def where(x: float, y: float) -> tuple[bool, bool]:
        """(inside the searched sea, within the margin of it)."""
        col, row = inv @ (x, y)
        r, c = int(row), int(col)
        inside = 0 <= r < grid.height and 0 <= c < grid.width and bool(imaged[r, c])
        near = inside or bool(imaged[max(0, r - margin) : r + margin + 1, max(0, c - margin) : c + margin + 1].any())
        return inside, near

    inside, near_edge, not_imaged = [], [], []
    for e in expected:
        # Either where the ship is or where its shift puts it: near the edge of the scene or of
        # the coastal buffer, the shift can move one inside and the other out.
        (in_true, near_true), (in_shifted, near_shifted) = where(*e.true_xy), where(e.x, e.y)
        if in_true or in_shifted:
            inside.append(e)
        elif near_true or near_shifted:
            near_edge.append(e)
        else:
            not_imaged.append(e)
    candidates = inside + near_edge

    if not detections or not candidates:
        return MatchResult([], list(detections), inside, near_edge + not_imaged)

    det_xy = np.array([(d.x, d.y) for d in detections])
    exp_xy = np.array([(e.x, e.y) for e in candidates])
    distance = np.linalg.norm(det_xy[:, None, :] - exp_xy[None, :, :], axis=2)
    relative = distance / np.array([e.tolerance_m for e in candidates])[None, :]
    rows, cols = linear_sum_assignment(np.where(relative <= 1, relative, 1e9))

    matches = [
        Match(detections[r], candidates[c], round(float(distance[r, c]), 1))
        for r, c in zip(rows, cols, strict=True)
        if relative[r, c] <= 1
    ]
    matched_det = {id(m.detection) for m in matches}
    matched_exp = {id(m.expected) for m in matches}
    return MatchResult(
        matches,
        [d for d in detections if id(d) not in matched_det],
        [e for e in inside if id(e) not in matched_exp],
        [e for e in near_edge if id(e) not in matched_exp] + not_imaged,
    )
