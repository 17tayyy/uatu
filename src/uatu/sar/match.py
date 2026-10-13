"""Pair SAR detections with AIS vessels. Detections left without a vessel are dark vessel candidates.

A moving ship does not appear where it is in a SAR image: its motion towards or away from
the radar adds a Doppler shift that the processor reads as a position along the track.
The ship is displaced along the satellite's heading by

    shift = -(R / V) * range_rate

R being the slant range, V the satellite speed (R / V ~ 110 s for Sentinel-1) and
range_rate the ship's velocity along the line of sight (positive moving away). 5 m/s of
range rate moves a ship ~550 m: AIS positions are shifted the same way before pairing.
"""

import math
from dataclasses import dataclass

import numpy as np
from rasterio.warp import transform
from scipy.optimize import linear_sum_assignment

from uatu.sar.ais import AisVessel
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


@dataclass(frozen=True, slots=True)
class Match:
    detection: Detection
    expected: Expected
    distance_m: float


@dataclass(frozen=True)
class MatchResult:
    matches: list[Match]
    dark: list[Detection]  # detections with no AIS vessel close enough
    undetected: list[Expected]  # AIS vessels in the imaged sea that the radar did not pick up
    not_imaged: list[Expected]  # AIS vessels outside the imaged sea (other frame, near the coast)


def expected_positions(
    vessels: list[AisVessel], geometry: AcquisitionGeometry, grid: Grid, doppler_sign: float = 1.0
) -> list[Expected]:
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
        if v.sog is not None and v.cog is not None:
            ground_range_rate = v.sog * KNOT * math.cos(math.radians(v.cog - geometry.look_deg))
            range_rate = ground_range_rate * math.sin(math.radians(incidence[i]))
            shift = -doppler_sign * slant_range[i] / geometry.satellite_speed * range_rate
        x = xs[i] + shift * math.sin(heading)
        y = ys[i] + shift * math.cos(heading)
        expected.append(Expected(v, float(x), float(y), round(shift, 1), (float(xs[i]), float(ys[i]))))
    return expected


def match(
    detections: list[Detection], expected: list[Expected], imaged: np.ndarray, grid: Grid, max_distance_m: float
) -> MatchResult:
    """Pair detections with expected AIS positions one-to-one, minimising total distance (Hungarian method).

    `imaged` marks the sea the detector actually searched; AIS vessels outside it could not
    have been detected and are reported apart.
    """
    inv = ~grid.transform

    def is_imaged(x: float, y: float) -> bool:
        col, row = inv @ (x, y)
        r, c = int(row), int(col)
        return 0 <= r < grid.height and 0 <= c < grid.width and bool(imaged[r, c])

    inside, not_imaged = [], []
    for e in expected:
        # Either where the ship is or where its shift puts it: near the edge of the scene or of
        # the coastal buffer, the shift can move one inside and the other out.
        (inside if is_imaged(e.x, e.y) or is_imaged(*e.true_xy) else not_imaged).append(e)

    if not detections or not inside:
        return MatchResult([], list(detections), inside, not_imaged)

    det_xy = np.array([(d.x, d.y) for d in detections])
    exp_xy = np.array([(e.x, e.y) for e in inside])
    distance = np.linalg.norm(det_xy[:, None, :] - exp_xy[None, :, :], axis=2)
    cost = np.where(distance <= max_distance_m, distance, 1e9)
    rows, cols = linear_sum_assignment(cost)

    matches = [
        Match(detections[r], inside[c], round(float(distance[r, c]), 1))
        for r, c in zip(rows, cols, strict=True)
        if distance[r, c] <= max_distance_m
    ]
    matched_det = {id(m.detection) for m in matches}
    matched_exp = {id(m.expected) for m in matches}
    return MatchResult(
        matches,
        [d for d in detections if id(d) not in matched_det],
        [e for e in inside if id(e) not in matched_exp],
        not_imaged,
    )
