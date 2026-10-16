import math

import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS

from uatu.sar.raster import Grid
from uatu.sar.wake import WakeParams, find_wake

HEADING, LOOK = 347.0, 77.0  # an ascending pass: north-north-west, looking east-north-east
PER_KNOT = 40.0  # metres of shift per knot straight away from the radar
PARAMS = WakeParams(max_speed_kn=25.0, lengths_m=(500.0, 1000.0))
SEA = 0.05  # linear sigma0


def _scene(course: float, speed_kn: float, wake: float | None, seed: int = 1):
    """Speckled sea around a ship at the origin, its echo shifted along the track, and its wake
    (`wake` times as bright as the sea) trailing 1.5 km behind where it really is."""
    half = math.ceil(PARAMS.chip_half_m(PER_KNOT) / 10)
    chip = Grid(CRS.from_epsg(32630), Affine(10, 0, -half * 10, 0, -10, half * 10), 2 * half, 2 * half)
    rng = np.random.default_rng(seed)
    image = rng.gamma(4.4, SEA / 4.4, chip.shape).astype(np.float32)  # GRD speckle, ~4.4 looks
    rows, cols = np.indices(chip.shape)
    xs, ys = chip.to_map(rows, cols)
    c = math.radians(course)
    behind = -(xs * math.sin(c) + ys * math.cos(c))
    across = xs * math.cos(c) - ys * math.sin(c)
    if wake is not None:
        image[(behind > 0) & (behind < 1500) & (np.abs(across) <= 20)] *= wake
    shift = -PER_KNOT * speed_kn * math.cos(math.radians(course - LOOK))
    echo = (shift * math.sin(math.radians(HEADING)), shift * math.cos(math.radians(HEADING)))
    image[np.hypot(xs - echo[0], ys - echo[1]) < 30] = 100 * SEA
    return image, chip, echo, shift


@pytest.mark.parametrize(
    ("course", "wake"),
    [(72.0, 1.5), (252.0, 1.5), (72.0, 0.6)],
    ids=["eastbound, bright wake", "westbound, bright wake", "eastbound, dark wake"],
)
def test_the_wake_tells_the_course_the_speed_and_where_the_ship_really_is(course, wake):
    image, chip, echo, shift = _scene(course, 12.0, wake)
    found = find_wake(image, chip, echo, HEADING, LOOK, PER_KNOT, PARAMS, echo_size_m=60)
    assert found is not None
    assert found.clear
    assert abs((found.course_deg - course + 180) % 360 - 180) <= 3
    assert found.speed_kn == pytest.approx(12.0, abs=1.5)
    assert found.shift_m == pytest.approx(shift, abs=60)
    assert math.hypot(found.x, found.y) < 60  # the ship, not its echo
    assert (found.contrast > 0) == (wake > 1)


def test_a_sea_without_a_wake_yields_none_that_stands_out():
    for seed in range(3):
        image, chip, echo, _ = _scene(72.0, 12.0, None, seed)
        found = find_wake(image, chip, echo, HEADING, LOOK, PER_KNOT, PARAMS, echo_size_m=60)
        assert found is None or not found.clear


def test_without_sea_around_there_is_nothing_to_look_at():
    image, chip, echo, _ = _scene(72.0, 12.0, 1.5)
    image[:] = np.nan
    assert find_wake(image, chip, echo, HEADING, LOOK, PER_KNOT, PARAMS) is None
