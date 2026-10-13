"""Ship detection on calibrated SAR backscatter with a CFAR detector.

Ships are strong radar reflectors (metal, corner-like structures) on a comparatively dark
sea. A CFAR (constant false alarm rate) detector flags pixels much brighter than the sea
around them, adapting to local sea state, then neighbouring flagged pixels are grouped
into objects. Detections near the coast are discarded: land, harbour structures and
breakwaters look just like ships.
"""

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from uatu.sar.raster import Grid


@dataclass(frozen=True, slots=True)
class Detection:
    lon: float
    lat: float
    x: float  # grid CRS, metres
    y: float
    pixels: int
    length_m: float  # rough size from the spread of the object's pixels; SAR blurs small targets
    peak_db: float  # brightest pixel, VV sigma0 in dB
    vv_db: float  # mean over the object
    vh_db: float
    background_db: float  # median VV of the sea around it

    @property
    def contrast_db(self) -> float:
        return self.peak_db - self.background_db


@dataclass(frozen=True, slots=True)
class CfarParams:
    guard_m: float = 500.0  # excluded around the pixel under test: must exceed the largest ship
    background_m: float = 1500.0  # window from which the sea's statistics are estimated
    k: float = 6.0  # threshold, in background standard deviations above the background mean
    coast_buffer_m: float = 300.0  # ignore everything this close to land
    merge_m: float = 30.0  # hits closer than this belong to the same object
    min_pixels: int = 3
    # Fainter objects are mostly speckle in calm, dark water near the radar's noise floor.
    min_peak_db: float = -5.0
    # Rough sea seen at low incidence is bright and spiky: CFAR flags its peaks, which stand
    # only ~6-8 dB above the surrounding sea, while ships stand 15-40 dB above it.
    min_contrast_db: float = 12.0
    # A very bright target smears into a cross of sidelobes that CFAR also flags: drop
    # objects this much fainter than a neighbour within the radius.
    sidelobe_radius_m: float = 2000.0
    sidelobe_contrast_db: float = 15.0


def detect(vv: np.ndarray, vh: np.ndarray, land: np.ndarray, grid: Grid, params: CfarParams) -> list[Detection]:
    """Ships in a scene: CFAR on VV at sea, grouped into objects, without faint or sidelobe ones."""
    valid = sea_mask(vv, land, grid, params.coast_buffer_m)
    hits = cfar(vv, valid, grid, params)
    found = [
        d
        for d in extract(hits, vv, vh, valid, grid, params.merge_m, params.background_m)
        if d.pixels >= params.min_pixels and d.peak_db >= params.min_peak_db and d.contrast_db >= params.min_contrast_db
    ]
    return suppress_sidelobes(found, params.sidelobe_radius_m, params.sidelobe_contrast_db)


def sea_mask(vv: np.ndarray, land: np.ndarray, grid: Grid, coast_buffer_m: float) -> np.ndarray:
    """Pixels with data that are at sea and at least coast_buffer_m from land."""
    buffer_px = 2 * round(coast_buffer_m / grid.resolution) + 1
    near_land = ndimage.maximum_filter(land, size=buffer_px)
    return np.isfinite(vv) & (vv > 0) & ~near_land


def cfar(image: np.ndarray, valid: np.ndarray, grid: Grid, params: CfarParams) -> np.ndarray:
    """Two-parameter CFAR: valid pixels brighter than mean + k·std of the surrounding sea.

    The sea's statistics come from the ring between the guard and background windows, and
    only from valid pixels, so land and areas outside the scene do not bias them.
    """
    guard = _odd(params.guard_m / grid.resolution)
    background = _odd(params.background_m / grid.resolution)

    def ring_sum(a: np.ndarray) -> np.ndarray:
        return ndimage.uniform_filter(a, background) * background**2 - ndimage.uniform_filter(a, guard) * guard**2

    x = np.where(valid, image, 0).astype(np.float32)
    count = ring_sum(valid.astype(np.float32))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = ring_sum(x) / count
        std = np.sqrt(np.maximum(ring_sum(x * x) / count - mean * mean, 0))
    enough_sea = count >= 0.5 * (background**2 - guard**2)
    return valid & enough_sea & (x > mean + params.k * std)


def extract(
    hits: np.ndarray,
    vv: np.ndarray,
    vh: np.ndarray,
    valid: np.ndarray,
    grid: Grid,
    merge_m: float,
    background_m: float,
) -> list[Detection]:
    """Group hits closer than merge_m into objects and describe each one, from its hit pixels only."""
    half = round(background_m / grid.resolution / 2)
    grow = max(0, round(merge_m / grid.resolution / 2))
    grouped = ndimage.binary_dilation(hits, iterations=grow) if grow else hits
    labels, _ = ndimage.label(grouped, structure=np.ones((3, 3)))
    found = []
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        rows, cols = np.nonzero((labels[window] == index) & hits[window])
        if len(rows) == 0:
            continue
        rows, cols = rows + window[0].start, cols + window[1].start
        weights = vv[rows, cols]
        row, col = np.average(rows, weights=weights), np.average(cols, weights=weights)
        around = (
            slice(max(0, round(row) - half), round(row) + half + 1),
            slice(max(0, round(col) - half), round(col) + half + 1),
        )
        sea = vv[around][valid[around] & ~hits[around]]
        found.append(
            (
                row,
                col,
                len(rows),
                _length(rows, cols) * grid.resolution,
                float(weights.max()),
                float(weights.mean()),
                float(np.nanmean(vh[rows, cols])) if np.isfinite(vh[rows, cols]).any() else np.nan,
                float(np.median(sea)) if sea.size else np.nan,
            )
        )
    if not found:
        return []
    r, c, pixels, lengths, peaks, vv_means, vh_means, backgrounds = zip(*found, strict=True)
    xs, ys = grid.to_map(np.array(r), np.array(c))
    lons, lats = grid.to_lonlat(xs, ys)
    return [
        Detection(
            float(lon), float(lat), float(x), float(y), n, round(length, 1), _db(peak), _db(vv_m), _db(vh_m), _db(bg)
        )
        for lon, lat, x, y, n, length, peak, vv_m, vh_m, bg in zip(
            lons, lats, xs, ys, pixels, lengths, peaks, vv_means, vh_means, backgrounds, strict=True
        )
    ]


def suppress_sidelobes(detections: list[Detection], radius_m: float, contrast_db: float) -> list[Detection]:
    """Drop detections much fainter than a neighbour within radius_m."""
    if not detections:
        return []
    xy = np.array([(d.x, d.y) for d in detections])
    peaks = np.array([d.peak_db for d in detections])
    tree = cKDTree(xy)
    keep = []
    for i, d in enumerate(detections):
        neighbours = tree.query_ball_point(xy[i], radius_m)
        if peaks[neighbours].max() - d.peak_db < contrast_db:
            keep.append(d)
    return keep


def _length(rows: np.ndarray, cols: np.ndarray) -> float:
    """Length in pixels of the object's major axis, from the second moments of its pixels."""
    if len(rows) < 2:
        return 1.0
    major_variance = np.linalg.eigvalsh(np.cov(np.vstack([cols, rows]).astype(float)))[-1]
    return max(1.0, 4 * float(np.sqrt(major_variance)))  # 4 standard deviations span a uniform segment


def _odd(pixels: float) -> int:
    n = max(3, round(pixels))
    return n if n % 2 else n + 1


def _db(linear: float) -> float:
    return round(10 * float(np.log10(linear)), 1) if linear > 0 else float("nan")  # NaN > 0 is False too
