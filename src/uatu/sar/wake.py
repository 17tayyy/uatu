"""Course and speed of a ship from its wake, without AIS.

A ship under way leaves a turbulent trail that changes the small waves the radar sees: a line
behind it, darker or brighter than the sea, up to kilometres long in calm water. The wake is
water, not moving with the ship, so unlike the ship it is not displaced by the Doppler shift
(see match). Two things follow:

- the wake's direction is the ship's course, and the side it trails off to says which way;
- the wake starts where the ship really is. The echo sits away from that point along the
  satellite's heading by shift = -(R / V) * range_rate, and range_rate follows from the
  course and the speed. With the course from the wake and the shift from where it starts,
  the speed follows too, except for ships moving almost along the satellite's track, whose
  range rate (and shift) is nearly nil whatever their speed.

The wake drifts with the surface current, though, so the speed is through the water, not over
the ground: a current of 1 m/s, as the Strait's, is 2 kn either way.

The search runs over every course and every point on the line through the echo along the
satellite's heading (where the ship can be) that some speed up to max_speed_kn would put
there. Each hypothesis is scored by how much darker, or brighter, a ray behind that point is
than the sea on both sides of it, against all the other lines of that orientation around
(wind rows, fronts, internal waves: some seas are full of lines), minus how much the same line
continues ahead of the point: a wake starts at its ship. The best score is then compared with
the best among as many points where no ship could be (it would need a negative speed): the
margin between the two says whether the wake stands out from what this sea yields by chance.

Tested blind on the AIS ships of six frames of the Strait and Alboran (83 under way, 41 at
anchor or moored): from CLEAR_MARGIN, 6 wakes, all right: the course within 16 degrees (4 within
10), the speed within 2.1 kn of AIS; none for a ship that was not moving. But that is 1 ship in
14: in a breeze most wakes do not show at all, and below that margin wakes are as often lines
of the sea that happen to start near an echo. Over shallow water (MIN_DEPTH_M), the seabed shows
through the sea and passes for wakes too.
"""

import math
import warnings
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from scipy import ndimage

from uatu.sar.annotation import AcquisitionGeometry
from uatu.sar.detect import Detection
from uatu.sar.raster import CalibratedBand, Grid

KNOT = 0.514444  # m/s


@dataclass(frozen=True, slots=True)
class WakeParams:
    max_speed_kn: float = 45.0
    lengths_m: tuple[float, ...] = (500.0, 1000.0, 2000.0)  # rays tried behind the ship
    ahead_m: float = 600.0  # how far ahead of the ship the line must not continue
    beyond_m: float = 500.0  # nor run on, past its end, into another target
    start_m: float = 60.0  # rays begin this far behind the ship (its hull, the echo's blur)
    half_widths_m: tuple[float, ...] = (10.0, 20.0, 40.0)  # half the width of the wake
    side_gap_m: float = 30.0  # between the wake and the sea it is compared with
    side_m: float = 150.0  # width of that sea, on each side
    angle_step_deg: float = 1.0
    null_stride: int = 8  # pixels between the lines sampled to tell how contrasted lines are anyway
    shift_tolerance_m: float = 40.0  # where the wake starts is known to about this
    echo_radius_m: float = 120.0  # around the echo, ignored: the ship's own bright blur
    bright_factor: float = 5.0  # first guess at what is not sea, before the sea's level is known
    target_factor: float = 3.0  # what is this many times brighter than the sea around is a target
    target_margin_m: float = 50.0  # ignored around them too
    ship_factor: float = 10.0  # targets this many times brighter than the sea are other vessels
    trail_factor: float = 1.5  # and what trails off them this much brighter than the sea is theirs
    cross_m: float = 1000.0  # the echo's sidelobes, ignored this far along and across the track
    cross_half_width_m: float = 40.0
    cross_factor: float = 30.0  # targets this many times brighter than the sea have their cross ignored too
    cross_max_m: float = 3000.0
    edge_m: float = 100.0  # ignored along the edges of the data (the swath, the coast)
    clip: float = 3.0  # brighter pixels count as this bright
    background_m: float = 400.0  # scale of the sea's own variations, divided out
    min_valid: float = 0.7  # of a ray's pixels, or it is not scored
    min_speed_cos: float = 0.25  # below this |cos(course - look)| the shift says little of the speed

    def chip_half_m(self, metres_per_knot: float) -> float:
        """Half the side of the image needed around an echo."""
        return self.max_speed_kn * metres_per_knot + max(self.lengths_m) + self.side_m + 200


DEFAULT_PARAMS = WakeParams()
# From this margin on, a wake was right every time in validation (see above).
CLEAR_MARGIN = 2.5
# In shallower water the seabed shows through the sea (banks, dredged channels): lines that pass for
# wakes. One did, in the inner Bay of Cadiz.
MIN_DEPTH_M = 20.0

DepthAt = Callable[[np.ndarray, np.ndarray], np.ndarray]  # lons, lats -> metres of water (NaN unknown)


def is_clear(margin: float, depth_m: float | None) -> bool:
    """Whether a wake can be told: it stands out clearly, and not over shallow water (or the depth
    is not known)."""
    return margin >= CLEAR_MARGIN and (depth_m is None or depth_m >= MIN_DEPTH_M)


@dataclass(frozen=True, slots=True)
class Wake:
    course_deg: float  # clockwise from north
    speed_kn: float | None  # None when the course is too close to the satellite's track to tell
    shift_m: float  # echo minus true position, along the satellite heading
    x: float  # where the ship really was (grid CRS, metres)
    y: float
    lon: float
    lat: float
    length_m: float  # of the ray that scored best
    contrast: float  # relative to the sea beside it: -0.3 is 30 % darker
    score: float  # how much the wake stands out from other lines of the sea around, in spreads
    margin: float  # over the best line where no ship could have been; see CLEAR_MARGIN
    depth_m: float | None = None  # of the water under it (median), when known

    @property
    def clear(self) -> bool:
        return is_clear(self.margin, self.depth_m)


def depth_under(lon: float, lat: float, course_deg: float, length_m: float, depth_at: DepthAt) -> float | None:
    """Median depth of the water under a wake starting at (lon, lat), behind a ship on `course_deg`."""
    behind = math.radians(course_deg + 180)
    along = np.linspace(0, length_m, 11)
    lons = lon + along * math.sin(behind) / (111_320 * math.cos(math.radians(lat)))
    lats = lat + along * math.cos(behind) / 111_320
    depths = np.asarray(depth_at(lons, lats), dtype=float)
    return round(float(np.nanmedian(depths)), 1) if np.isfinite(depths).any() else None


def metres_per_knot(slant_range_m: float, satellite_speed: float, incidence_deg: float) -> float:
    """Shift of a ship moving 1 kn straight away from the radar."""
    return slant_range_m / satellite_speed * KNOT * math.sin(math.radians(incidence_deg))


def find_wake(
    image: np.ndarray,
    chip: Grid,
    echo_xy: tuple[float, float],
    heading_deg: float,
    look_deg: float,
    shift_per_knot: float,
    params: WakeParams = DEFAULT_PARAMS,
    echo_size_m: float = 0.0,
    doppler_sign: float = 1.0,
) -> Wake | None:
    """The wake best explaining the sea around an echo, however faint (see Wake.margin); None if
    there is too little sea around to look.

    image: linear sigma0 on `chip`, NaN where there is no sea. shift_per_knot: see metres_per_knot.
    """
    res = chip.resolution
    ex, ey = echo_xy
    echo_radius = max(params.echo_radius_m, min(echo_size_m, 300.0))
    prepared = _normalised(image, chip, echo_xy, echo_radius, heading_deg, params)
    if prepared is None:
        return None
    norm, others = prepared
    k = doppler_sign * shift_per_knot
    s_max = params.max_speed_kn * abs(k)
    shifts = np.arange(-math.ceil(s_max / res), math.ceil(s_max / res) + 1) * res
    az = np.array([math.sin(math.radians(heading_deg)), math.cos(math.radians(heading_deg))])
    # Hypotheses are arrays: (dark or bright, half width, ray length, shift).
    hw = np.array([max(0, round(w / res)) for w in params.half_widths_m])[:, None, None]
    gap, side = round(params.side_gap_m / res), max(1, round(params.side_m / res))
    band = (hw, gap, side)
    lengths = np.array([round(length / res) for length in params.lengths_m])
    start, ahead = max(1, round(params.start_m / res)), round(params.ahead_m / res)
    far = np.append(lengths, ahead)[None, :, None]  # the rays behind, then the one ahead
    extend = round(params.beyond_m / res)
    signs = np.array([-1, 1])[:, None, None, None]  # dark wake, bright wake
    reach_p = int(hw.max()) + gap + side

    best: tuple[float, ...] | None = None
    chance = -math.inf
    for theta in np.arange(0.0, 180.0, params.angle_step_deg):
        t = math.radians(theta)
        u = np.array([math.sin(t), math.cos(t)])  # along the line, towards `theta`
        n = np.array([math.cos(t), -math.sin(t)])  # across it
        # Where the ship would be for each shift, in (across, along) pixels from the echo.
        p_off = -shifts * float(az @ n) / res
        q_off = -shifts * float(az @ u) / res
        half_p = math.ceil(np.abs(p_off).max()) + reach_p + 1
        half_q = math.ceil(np.abs(q_off).max()) + int(far.max()) + 1
        ps, qs = np.arange(-half_p, half_p + 1) * res, np.arange(-half_q, half_q + 1) * res
        xs = ex + ps[None, :] * n[0] + qs[:, None] * u[0]
        ys = ey + ps[None, :] * n[1] + qs[:, None] * u[1]
        cols, rows = ~chip.transform @ (xs, ys)
        rotated = ndimage.map_coordinates(norm, [rows - 0.5, cols - 0.5], order=1, cval=np.nan, prefilter=False)
        sums, counts = _integral(rotated)
        targets = _integral(ndimage.map_coordinates(others, [rows - 0.5, cols - 0.5], order=0, cval=0.0))[0]
        jp = np.rint(p_off).astype(int) + half_p
        ip = np.rint(q_off).astype(int) + half_q
        # Lines of this orientation all around: how contrasted lines are in this sea anyway.
        stride = params.null_stride
        every_i, every_j = (a.ravel() for a in np.mgrid[0 : rotated.shape[0] : stride, 0 : rotated.shape[1] : stride])
        centre, spread = _spread(_contrast(sums, counts, *_rows(every_i, 1, start, far), every_j, band, params))

        for course, sense in ((theta, 1), ((theta + 180) % 360, -1)):
            cos_look = math.cos(math.radians(course - look_deg))
            # Shifts some speed in [0, max_speed_kn] gives, within the tolerance.
            limit = -k * params.max_speed_kn * cos_look
            tol = params.shift_tolerance_m
            ok = (shifts >= min(0, limit) - tol) & (shifts <= max(0, limit) + tol)
            # The mirror image: as many places, none of which a ship could be at (it would need
            # a negative speed). The best line found there is what this sea yields by chance.
            impossible = (shifts >= min(0, -limit) - tol) & (shifts <= max(0, -limit) + tol) & ~ok
            c = _contrast(sums, counts, *_rows(ip, -sense, start, far[:, :-1]), jp, band, params)
            # A wake fades into the sea; a line that runs on into another target is that target's.
            beyond = _rows(ip, -sense, far[:, :-1], far[:, :-1] + extend)
            c = np.where(_box(targets, *beyond, jp - hw - gap, jp + hw + gap + 1) > 0, np.nan, c)
            z = (c - centre[:, :-1]) / spread[:, :-1]
            a = _contrast(sums, counts, *_rows(ip, sense, start, ahead), jp, band, params)
            a = (a - centre[:, -1:]) / spread[:, -1:]
            score = signs * z - np.maximum(0, signs * a)
            score = np.where(np.isfinite(score), score, -np.inf)
            chance = max(chance, float(score[..., impossible].max(initial=-np.inf)))
            score = np.where(ok, score, -np.inf)
            i = np.unravel_index(int(np.argmax(score)), score.shape)
            if best is None or score[i] > best[0]:
                _, h, ray, s = i
                best = (float(score[i]), course, float(shifts[s]), lengths[ray] * res, float(c[h, ray, s]), cos_look)

    if best is None or not math.isfinite(best[0]):
        return None
    score, course, shift, length_m, contrast, cos_look = best
    speed = None
    if abs(cos_look) >= params.min_speed_cos:
        speed = max(0.0, -shift / (k * cos_look))
    x, y = float(ex - shift * az[0]), float(ey - shift * az[1])
    lons, lats = chip.to_lonlat(np.array([x]), np.array([y]))
    return Wake(
        course_deg=round(float(course), 1),
        speed_kn=None if speed is None else round(speed, 1),
        shift_m=round(shift, 1),
        x=x,
        y=y,
        lon=float(lons[0]),
        lat=float(lats[0]),
        length_m=float(length_m),
        contrast=round(contrast, 3),
        score=round(score, 2),
        margin=round(score - chance, 2) if math.isfinite(chance) else 0.0,
    )


def find_wakes(
    zip_path: Path,
    detections: list[Detection],
    geometry: AcquisitionGeometry,
    grid: Grid,
    skip: Collection[Detection] = (),
    params: WakeParams = DEFAULT_PARAMS,
    doppler_sign: float = 1.0,
    depth_at: DepthAt | None = None,
) -> dict[Detection, Wake]:
    """The wake of each detection of a scene but those in `skip`, from the product at full resolution
    (the grid's) around it, with the depth of the water under it if `depth_at` tells. A few seconds each."""
    todo = [d for d in detections if d not in skip]
    if not todo:
        return {}
    _, slant_range, incidence = geometry.at(np.array([d.lon for d in todo]), np.array([d.lat for d in todo]))
    res = grid.resolution
    found = {}
    with rasterio.Env(GDAL_CACHEMAX=64), CalibratedBand(zip_path, "VV") as band:
        for d, r, inc in zip(todo, slant_range, incidence, strict=True):
            per_knot = metres_per_knot(float(r), geometry.satellite_speed, float(inc))
            half = math.ceil(params.chip_half_m(per_knot) / res)
            chip = Grid(grid.crs, Affine(res, 0, d.x - half * res, 0, -res, d.y + half * res), 2 * half, 2 * half)
            wake = find_wake(
                band.warp(chip),
                chip,
                (d.x, d.y),
                geometry.heading_deg,
                geometry.look_deg,
                per_knot,
                params,
                d.length_m,
                doppler_sign,
            )
            if wake is not None:
                if depth_at is not None:
                    depth = depth_under(wake.lon, wake.lat, wake.course_deg, wake.length_m, depth_at)
                    wake = replace(wake, depth_m=depth)
                found[d] = wake
    return found


def _normalised(
    image: np.ndarray,
    chip: Grid,
    echo_xy: tuple[float, float],
    echo_radius_m: float,
    heading_deg: float,
    params: WakeParams,
) -> tuple[np.ndarray, np.ndarray] | None:
    """The image over the sea's local level (1 = ordinary sea), NaN where it should not count:
    no data, other bright targets, the echo and the cross of sidelobes a bright echo smears into
    along the satellite's track and across it. And where the other targets are (1, else 0). None
    if there is too little sea."""
    valid = np.isfinite(image) & (image > 0)
    if valid.mean() < 0.3:
        return None
    res = chip.resolution
    # Targets are told by the mean of 3x3 pixels: single speckle pixels of a bright wake would pass.
    filled = np.where(valid, image, 0.0)
    smooth = ndimage.uniform_filter(filled, 3) / np.maximum(ndimage.uniform_filter(valid * 1.0, 3), 1e-6)
    rough = valid & ~ndimage.binary_dilation(smooth > params.bright_factor * np.median(image[valid]), iterations=3)
    scale = params.background_m / res
    level = ndimage.gaussian_filter(np.where(rough, image, 0.0), scale)
    level /= np.maximum(ndimage.gaussian_filter(rough * 1.0, scale), 1e-6)
    norm = filled / np.maximum(level, 1e-12)

    bright = valid & (smooth > params.target_factor * level)
    rows, cols = np.indices(image.shape)
    xs, ys = chip.to_map(rows, cols)
    dx, dy = xs - echo_xy[0], ys - echo_xy[1]
    near_echo = np.hypot(dx, dy) < echo_radius_m
    labels, count = ndimage.label(bright)
    index = np.arange(1, count + 1)
    strongest = np.asarray(ndimage.maximum(smooth / np.maximum(level, 1e-12), labels, index)) if count else index
    # The other vessels (not the echo, nor what is connected to it, nor a clump of speckle in a
    # bright wake), and what they trail off into, brighter than the sea all along: their wakes, the
    # smear of the brightest. The echo's own is not followed: its ship's wake starts away from it,
    # where the ship really is.
    ships = index[(strongest >= params.ship_factor) & ~np.isin(index, labels[near_echo])]
    wide = ndimage.uniform_filter(filled, 5) / np.maximum(ndimage.uniform_filter(valid * 1.0, 5), 1e-6)
    others = ndimage.binary_propagation(np.isin(labels, ships), mask=valid & (wide > params.trail_factor * level))
    margin = round(params.target_margin_m / res)
    targets = ndimage.binary_dilation(bright | others, iterations=margin)
    # Every bright target smears into a cross of sidelobes, along the satellite's track and across
    # it: lines that would pass for wakes. The echo's own, and those of the others in the chip.
    h = math.radians(heading_deg)
    along, across = dx * math.sin(h) + dy * math.cos(h), dx * math.cos(h) - dy * math.sin(h)
    cross = _cross(along, across, 0.0, 0.0, params)
    if count:
        centres = ndimage.center_of_mass(bright, labels, index)
        for (row, col), peak in zip(centres, strongest, strict=True):
            if peak >= params.cross_factor:
                ox, oy = chip.to_map(np.array(row), np.array(col))
                a0 = (ox - echo_xy[0]) * math.sin(h) + (oy - echo_xy[1]) * math.cos(h)
                c0 = (ox - echo_xy[0]) * math.cos(h) - (oy - echo_xy[1]) * math.sin(h)
                # The brighter the target, the further its sidelobes stand out of the sea.
                reach = min(params.cross_max_m, params.cross_m * math.sqrt(peak / params.cross_factor))
                cross |= _cross(along, across, float(a0), float(c0), params, reach)
    # The edge of the swath, and of the sea, makes a line of its own.
    inside = ndimage.binary_erosion(valid, iterations=round(params.edge_m / res), border_value=1)
    keep = inside & ~targets & ~cross & ~near_echo
    others = ndimage.binary_dilation(others, iterations=margin)
    return np.where(keep, np.minimum(norm, params.clip), np.nan).astype(np.float32), others.astype(np.float32)


def _cross(
    along: np.ndarray, across: np.ndarray, a0: float, c0: float, params: WakeParams, reach: float | None = None
) -> np.ndarray:
    """Where the sidelobes of a target at (a0, c0) (along and across the satellite's track) fall,
    up to `reach` metres from it (params.cross_m by default)."""
    da, dc = np.abs(along - a0), np.abs(across - c0)
    width, reach = params.cross_half_width_m, params.cross_m if reach is None else reach
    return ((dc < width) & (da < reach)) | ((da < width) & (dc < reach))


def _integral(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Summed-area tables of the values and of how many there are, ignoring NaN."""
    valid = np.isfinite(a)
    sums = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    counts = np.zeros_like(sums)
    sums[1:, 1:] = np.where(valid, a, 0).cumsum(0).cumsum(1)
    counts[1:, 1:] = valid.cumsum(0).cumsum(1)
    return sums, counts


def _box(table: np.ndarray, i0: np.ndarray, i1: np.ndarray, j0: np.ndarray, j1: np.ndarray) -> np.ndarray:
    """Sums over rows [i0, i1) and columns [j0, j1), clipped to the table."""
    h, w = table.shape[0] - 1, table.shape[1] - 1
    i0, i1 = np.clip(i0, 0, h), np.clip(i1, 0, h)
    j0, j1 = np.clip(j0, 0, w), np.clip(j1, 0, w)
    return table[i1, j1] - table[i0, j1] - table[i1, j0] + table[i0, j0]


def _rows(ip: np.ndarray, sense: int, near: int | np.ndarray, far: int | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rows of the rotated image from `near` (excluded) to `far` pixels from each apex, towards
    increasing rows for sense +1, decreasing for -1."""
    return (ip + near + 1, ip + far + 1) if sense == 1 else (ip - far, ip - near)


def _contrast(
    sums: np.ndarray,
    counts: np.ndarray,
    i0: np.ndarray,
    i1: np.ndarray,
    jp: np.ndarray,
    band: tuple[int | np.ndarray, int, int],
    params: WakeParams,
) -> np.ndarray:
    """Relative contrast of rows [i0, i1) of the columns within hw of each apex's against the sea on
    both sides of them, `gap` columns off and `side` wide: -0.3 is 30 % darker. NaN where too little
    of either is sea. band: (hw, gap, side)."""
    hw, gap, side = band
    wake_s = _box(sums, i0, i1, jp - hw, jp + hw + 1)
    wake_n = _box(counts, i0, i1, jp - hw, jp + hw + 1)
    left = jp - hw - gap
    right = jp + hw + gap + 1
    sea_s = _box(sums, i0, i1, left - side, left) + _box(sums, i0, i1, right, right + side)
    sea_n = _box(counts, i0, i1, left - side, left) + _box(counts, i0, i1, right, right + side)
    rows = i1 - i0
    enough = (wake_n >= params.min_valid * rows * (2 * hw + 1)) & (sea_n >= params.min_valid * rows * 2 * side)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(enough, (wake_s / wake_n) / (sea_s / sea_n) - 1, np.nan)


def _spread(contrasts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median and robust standard deviation along the last axis, ignoring NaN."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all NaN
        median = np.nanmedian(contrasts, axis=-1, keepdims=True)
        spread = 1.4826 * np.nanmedian(np.abs(contrasts - median), axis=-1, keepdims=True)
    enough = np.isfinite(contrasts).sum(axis=-1, keepdims=True) >= 50
    return np.where(enough, median, 0.0), np.where(enough & (spread > 0), spread, np.inf)
