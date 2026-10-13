import math
from datetime import UTC, datetime, timedelta

import numpy as np
from affine import Affine
from rasterio.crs import CRS

from uatu.sar.ais import AisVessel, Report, predict
from uatu.sar.annotation import AcquisitionGeometry
from uatu.sar.catalog import _scene, group_passes
from uatu.sar.detect import CfarParams, Detection, detect, suppress_sidelobes
from uatu.sar.match import Expected, expected_positions, match
from uatu.sar.raster import Grid, SigmaLut

T0 = datetime(2026, 10, 13, 18, 25, tzinfo=UTC)
GRID = Grid(CRS.from_epsg(32630), Affine(10, 0, 280_000, 0, -10, 3_990_000), 400, 400)


def test_sigma_lut_interpolates_bilinearly():
    lut = SigmaLut(lines=np.array([0.0, 10.0]), values=np.array([[1.0, 3.0], [5.0, 7.0]]), pixels=np.array([0.0, 10.0]))
    a = lut.at(np.array([0, 5, 10]), width=11)
    assert a[0, 0] == 1 and a[0, 10] == 3 and a[2, 0] == 5 and a[2, 10] == 7
    assert a[1, 5] == 4  # centre: mean of the four corners


def speckled_sea(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).exponential(0.01, GRID.shape).astype(np.float32)  # ~ -20 dB


def test_detects_a_ship_and_ignores_the_coast():
    vv = speckled_sea()
    vv[200:203, 196:206] = 5.0  # 100 m ship at sea, +7 dB
    vv[20:23, 20:30] = 5.0  # same, but next to land
    land = np.zeros(GRID.shape, dtype=bool)
    land[:10, :] = True
    found = detect(vv, np.full_like(vv, np.nan), land, GRID, CfarParams(guard_m=300, background_m=900))
    assert len(found) == 1
    ship = found[0]
    col, row = ~GRID.transform @ (ship.x, ship.y)
    assert abs(row - 201.5) < 1 and abs(col - 201) < 1
    assert 80 <= ship.length_m <= 130
    assert 25 < ship.contrast_db < 30  # +7 dB on a ~-20 dB sea


def test_low_contrast_spikes_are_not_ships():
    # Uniform -10 dB sea: CFAR flags anything above it, but these spikes stand only ~8 dB out.
    vv = (0.1 * (1 + 0.01 * np.random.default_rng(1).standard_normal(GRID.shape))).astype(np.float32)
    vv[200:203, 196:206] = 0.6
    land = np.zeros(GRID.shape, dtype=bool)
    params = CfarParams(guard_m=300, background_m=900)
    assert detect(vv, np.full_like(vv, np.nan), land, GRID, params) == []
    vv[200:203, 196:206] = 5.0  # ~17 dB out: a ship
    assert len(detect(vv, np.full_like(vv, np.nan), land, GRID, params)) == 1


def det(x: float, y: float, peak_db: float) -> Detection:
    return Detection(0, 0, x, y, 10, 50, peak_db, peak_db, float("nan"), -20.0)


def test_sidelobes_of_a_bright_target_are_dropped():
    bright, sidelobe, far = det(0, 0, 25), det(800, 0, 5), det(5000, 0, 5)
    assert suppress_sidelobes([bright, sidelobe, far], 2000, 15) == [bright, far]


def test_predict_interpolates_between_reports():
    reports = [Report(T0, -5.6, 36.0, 10, 0), Report(T0 + timedelta(minutes=2), -5.6, 36.02, 10, 0)]
    lon, lat, _, _, gap = predict(reports, T0 + timedelta(seconds=30))
    assert (lon, round(lat, 4), gap) == (-5.6, 36.005, 30)


def test_predict_dead_reckons_from_the_nearest_report():
    reports = [Report(T0, -5.6, 36.0, 10, 0)]  # 10 knots due north
    lon, lat, _, _, gap = predict(reports, T0 + timedelta(seconds=60))
    assert lon == -5.6 and gap == 60
    assert abs((lat - 36.0) * 111_320 - 10 * 0.514444 * 60) < 1  # ~309 m
    assert predict(reports, T0 + timedelta(minutes=11)) is None


def geometry(heading_deg: float = 180.0) -> AcquisitionGeometry:
    """Flat geometry: 850 km slant range, 35° incidence, everywhere."""
    corners = np.array([(-7.0, 35.0), (-4.0, 35.0), (-7.0, 37.0), (-4.0, 37.0)])
    return AcquisitionGeometry(T0, heading_deg, 7600.0, corners, np.zeros(4), np.full(4, 850e3), np.full(4, 35.0))


def vessel(sog: float | None, cog: float | None) -> AisVessel:
    return AisVessel(1, "TEST", 70, 150, -5.6, 36.0, sog, cog, T0, 0.0)


def test_moving_ships_are_shifted_along_the_track():
    g = geometry(heading_deg=180)  # flying south, looking west (270°)
    still, away = expected_positions([vessel(0, 0), vessel(10, 270)], g, GRID)
    range_rate = 10 * 0.514444 * math.sin(math.radians(35))
    assert still.shift_m == 0
    assert abs(away.shift_m + 850e3 / 7600 * range_rate) < 0.1  # ~ -330 m
    assert abs((away.y - still.y) - (-away.shift_m)) < 0.1  # negative shift along a southward track = north
    assert abs(away.x - still.x) < 1e-6
    across, _ = expected_positions([vessel(10, 0), vessel(None, None)], g, GRID)
    assert abs(across.shift_m) < 1e-6  # moving along the track: no range rate


def test_match_pairs_one_to_one_and_reports_leftovers():
    e1 = Expected(vessel(0, 0), 281_000, 3_989_000, 0, (281_000, 3_989_000))
    e2 = Expected(vessel(0, 0), 281_400, 3_989_000, 0, (281_400, 3_989_000))
    outside = Expected(vessel(0, 0), 0, 0, 0, (0, 0))
    edge = Expected(vessel(10, 270), 279_900, 3_989_000, -300, (280_100, 3_989_000))  # shifted out of the grid
    d1, d2, dark = det(281_100, 3_989_000, 10), det(281_350, 3_989_000, 10), det(283_500, 3_987_000, 10)
    imaged = np.ones(GRID.shape, dtype=bool)
    result = match([d1, d2, dark], [e1, e2, outside, edge], imaged, GRID, max_distance_m=500)
    assert {(m.detection, m.expected) for m in result.matches} == {(d1, e1), (d2, e2)}
    assert result.dark == [dark]
    assert result.undetected == [edge]
    assert result.not_imaged == [outside]


def product(name: str, start: str) -> dict:
    return {
        "Id": name,
        "Name": name,
        "ContentDate": {"Start": start, "End": start},
        "ContentLength": 10**9,
        "Online": True,
        "GeoFootprint": {"type": "Polygon", "coordinates": [[[-7, 35], [-4, 35], [-4, 37], [-7, 37], [-7, 35]]]},
        "Attributes": [{"Name": "orbitDirection", "Value": "ASCENDING"}, {"Name": "relativeOrbitNumber", "Value": 147}],
    }


def test_catalog_scenes_group_into_passes():
    names = [
        "S1C_IW_GRDH_1SDV_20261013T182500_20261013T182525_009860_013A00_AAAA.SAFE",
        "S1C_IW_GRDH_1SDV_20261013T182500_20261013T182525_009860_013A00_BBBB_COG.SAFE",
        "S1C_IW_GRDH_1SDV_20261013T182525_20261013T182550_009860_013A00_CCCC_COG.SAFE",
    ]
    scenes = [_scene(product(n, "2026-10-13T18:25:00.000000Z")) for n in names]
    assert scenes[0].frame == scenes[1].frame and scenes[1].is_cog and not scenes[0].is_cog
    assert (scenes[0].platform, scenes[0].datatake, scenes[0].relative_orbit) == ("S1C", "013A00", 147)
    assert list(group_passes(scenes)) == ["013A00"]
