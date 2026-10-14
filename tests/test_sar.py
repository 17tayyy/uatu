import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
from affine import Affine
from PIL import Image
from rasterio.crs import CRS
from rasterio.windows import Window

from uatu.config import STRAIT_OF_GIBRALTAR, bbox_polygon
from uatu.sar.ais import AisVessel, Coverage, Report, predict
from uatu.sar.annotation import AcquisitionGeometry
from uatu.sar.catalog import _scene, group_passes
from uatu.sar.detect import CfarParams, Detection, detect, suppress_sidelobes
from uatu.sar.match import Expected, expected_positions, match, split_by_coverage
from uatu.sar.pipeline import _block_mean, _inside, _pad
from uatu.sar.raster import Grid, SigmaLut
from uatu.sar.report import write_overlay

T0 = datetime(2026, 10, 13, 18, 25, tzinfo=UTC)
GRID = Grid(CRS.from_epsg(32630), Affine(10, 0, 280_000, 0, -10, 3_990_000), 400, 400)


def test_sigma_lut_interpolates_bilinearly():
    lut = SigmaLut(lines=np.array([0.0, 10.0]), values=np.array([[1.0, 3.0], [5.0, 7.0]]), pixels=np.array([0.0, 10.0]))
    a = lut.at(np.array([0, 5, 10]), np.arange(11))
    assert a[0, 0] == 1 and a[0, 10] == 3 and a[2, 0] == 5 and a[2, 10] == 7
    assert a[1, 5] == 4  # centre: mean of the four corners
    assert np.array_equal(lut.at(np.array([5]), np.arange(3, 8)), a[1:2, 3:8])  # any window of columns


def test_grid_windows_tiles_and_coarsening():
    sub = GRID.window(Window(100, 50, 30, 20))
    assert sub.shape == (20, 30)
    assert np.allclose(sub.to_map(np.array([0]), np.array([0])), GRID.to_map(np.array([50]), np.array([100])))
    tiles = list(GRID.tiles(150))
    covered = np.zeros(GRID.shape, dtype=int)
    for w in tiles:
        covered[w.toslices()] += 1
    assert len(tiles) == 9 and (covered == 1).all()  # 150 + 150 + 100 per side, no overlap
    coarse = GRID.coarsened(8)
    assert coarse.shape == (50, 50) and coarse.resolution == 80
    assert (coarse.transform.c, coarse.transform.f) == (GRID.transform.c, GRID.transform.f)  # same origin


def test_block_mean_ignores_nan_and_overhang():
    a = np.array([[1, 3, np.nan], [5, 7, 2]], dtype=np.float32)
    assert np.array_equal(_block_mean(a, 2), np.array([[4, 2]], dtype=np.float32))


def test_tiles_keep_only_detections_centred_in_their_core():
    core = Window(100, 100, 50, 50)
    padded = _pad(core, 20, GRID)
    assert (padded.col_off, padded.row_off, padded.width, padded.height) == (80, 80, 90, 90)
    assert _pad(Window(0, 0, 50, 50), 20, GRID).col_off == 0  # clipped at the grid edge
    xs, ys = GRID.to_map(np.array([120, 160]), np.array([120, 120]))
    inside, outside = det(xs[0], ys[0], 10), det(xs[1], ys[1], 10)
    assert _inside(inside, GRID, core) and not _inside(outside, GRID, core)


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


def test_objects_dark_in_vh_are_not_ships():
    vv = speckled_sea()
    vv[200:203, 196:206] = 5.0
    land = np.zeros(GRID.shape, dtype=bool)
    params = CfarParams(guard_m=300, background_m=900)
    vh = np.full_like(vv, 0.002)  # -27 dB everywhere: nothing there in cross-polarisation
    assert detect(vv, vh, land, GRID, params) == []
    vh[200:203, 196:206] = 0.1  # -10 dB: a ship
    assert len(detect(vv, vh, land, GRID, params)) == 1


def det(x: float, y: float, peak_db: float) -> Detection:
    return Detection(0, 0, x, y, 10, 50, peak_db, peak_db, float("nan"), -20.0)


def test_sidelobes_of_a_bright_target_are_dropped():
    bright, sidelobe, far = det(0, 0, 25), det(800, 0, 5), det(5000, 0, 5)
    assert suppress_sidelobes([bright, sidelobe, far], 2000, 15) == [bright, far]


def test_predict_interpolates_between_reports():
    reports = [Report(T0, -5.6, 36.0, 10, 0), Report(T0 + timedelta(minutes=2), -5.6, 36.02, 10, 0)]
    lon, lat, _, _, gap, _ = predict(reports, T0 + timedelta(seconds=30))
    assert (lon, round(lat, 4), gap) == (-5.6, 36.005, 30)


def test_predict_dead_reckons_from_the_nearest_report():
    reports = [Report(T0, -5.6, 36.0, 10, 0)]  # 10 knots due north
    lon, lat, _, _, gap, _ = predict(reports, T0 + timedelta(seconds=60))
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


def at(x: float, y: float, tolerance: float = 500, shift: float = 0) -> Expected:
    return Expected(vessel(0, 0), x + shift, y, shift, (x, y), tolerance)


def test_match_pairs_one_to_one_and_reports_leftovers():
    e1, e2, outside = at(281_000, 3_989_000), at(281_400, 3_989_000), at(0, 0)
    edge = at(280_100, 3_989_000, shift=-200)  # shifted out of the grid
    d1, d2, dark = det(281_100, 3_989_000, 10), det(281_350, 3_989_000, 10), det(283_500, 3_987_000, 10)
    imaged = np.ones(GRID.shape, dtype=bool)
    result = match([d1, d2, dark], [e1, e2, outside, edge], imaged, GRID)
    assert {(m.detection, m.expected) for m in result.matches} == {(d1, e1), (d2, e2)}
    assert result.dark == [dark]
    assert result.undetected == [edge]
    assert result.not_imaged == [outside]


def test_vessels_just_outside_the_imaged_sea_can_still_pair():
    imaged = np.zeros(GRID.shape, dtype=bool)
    imaged[:, :200] = True  # the scene ends at x = 282 000
    just_out, far_out = at(282_300, 3_988_000), at(283_900, 3_988_000)
    seen = det(281_900, 3_988_000, 10)  # the radar saw it 400 m from its AIS position, inside the scene
    result = match([seen], [just_out, far_out], imaged, GRID, edge_margin_m=1000)
    assert [(m.detection, m.expected) for m in result.matches] == [(seen, just_out)]
    assert result.undetected == []
    assert result.not_imaged == [far_out]
    unmatched = match([], [just_out], imaged, GRID, edge_margin_m=1000)
    assert unmatched.not_imaged == [just_out]  # not counted as missed by the radar


def test_tolerance_grows_with_speed_and_report_age():
    g = geometry()
    lon, lat = -5.4189, 36.0121  # centre of GRID
    fresh = AisVessel(1, None, None, None, lon, lat, 0.0, 0.0, T0, 10.0)
    stale_fast = AisVessel(2, None, None, None, lon, lat, 24.0, 0.0, T0, 500.0)  # fast ferry, 500 s old
    e_fresh, e_stale = expected_positions([fresh, stale_fast], g, GRID, base_tolerance_m=1000, drift_fraction=0.25)
    assert e_fresh.tolerance_m == 1000
    assert e_stale.tolerance_m == round(1000 + 0.25 * 24 * 0.514444 * 500, 1)  # ~2.5 km

    imaged = np.ones(GRID.shape, dtype=bool)
    far = det(e_stale.x + 2000, e_stale.y, 10)
    assert len(match([far], [e_stale], imaged, GRID).matches) == 1
    assert match([far], [e_fresh], imaged, GRID).matches == []


def test_unpaired_detections_without_ais_coverage_are_not_dark():
    lonely, busy = det(282_000, 3_988_000, 10), det(283_000, 3_988_000, 10)
    result = match([lonely, busy], [], np.ones(GRID.shape, dtype=bool), GRID)
    split = split_by_coverage(result, [Coverage(0, 0), Coverage(40, 400)], min_vessels=3, min_reports_per_vessel=3)
    assert split.dark == [busy]
    assert split.no_coverage == [lonely]
    assert split.ais_nearby[lonely] == Coverage(0, 0)


def test_vessels_heard_only_now_and_then_are_no_coverage():
    behind_the_rock, lane = det(282_000, 3_988_000, 10), det(283_000, 3_988_000, 10)
    result = match([behind_the_rock, lane], [], np.ones(GRID.shape, dtype=bool), GRID)
    split = split_by_coverage(result, [Coverage(6, 7), Coverage(16, 299)], min_vessels=3, min_reports_per_vessel=3)
    assert (split.dark, split.no_coverage) == ([lane], [behind_the_rock])


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


def test_worker_skips_scenes_that_barely_cover_the_area():
    from shapely import box

    from uatu.sar.catalog import Scene, worth_processing

    aoi = box(-6.5, 35.5, -4.5, 36.5)

    def scene(id_: str, footprint) -> Scene:
        return Scene(id_, f"S1C_{id_}", T0, T0, "ASCENDING", 147, 0, True, footprint)

    big, sliver = scene("a", box(-7, 35, -5, 37)), scene("b", box(-4.6, 35, -4, 37))  # 75% and 5%
    assert worth_processing([big, sliver], aoi, min_coverage=0.1) == [big]


def test_silent_vessels_are_placed_across_their_silence():
    from datetime import timedelta as td

    reports = [Report(T0, -5.6, 36.0, 10, 0), Report(T0 + td(hours=2), -5.6, 36.2, 10, 0)]
    middle = T0 + td(hours=1)
    assert predict(reports, middle) is None  # no report within 10 min: not placed for pairing
    lon, lat, _, _, gap, _ = predict(reports, middle, max_gap=td(hours=3))
    assert (lon, round(lat, 3), gap) == (-5.6, 36.1, 3600)


def test_scene_notification_separates_reception_gaps_from_silent_vessels():
    from uatu.sar.report import scene_notification
    from uatu.sar.store import Candidate, SceneReport

    def candidate(mmsi=None, silent=None, sanctioned=None):
        return Candidate(36.1, -5.3, 150.0, T0, mmsi, "SPARTAN" if mmsi else None, silent, 900.0, sanctioned)

    report = SceneReport(
        "S1C_IW_GRDH_TEST", T0, 0.42, 38, 12, 18, 1,
        [candidate(), candidate(1, 95.0, "EU-RUS"), candidate(2, 12.0)],
    )  # fmt: skip
    title, message, priority, link = scene_notification(report)
    assert title == "Pasada S1C 13/10 18:25 UTC: 2 barcos sin AIS"
    assert "1 explicado por huecos de recepción" in message
    assert "Se calló: SPARTAN ⚠ SANCIONADO (EU-RUS), 1 h 35 min sin emitir" in message
    assert "Sin identificar" in message
    assert priority == "high" and link is not None

    # Off the lists, but run by a sanctioned company: as urgent. On other watchlists: said, not urgent.
    managed = Candidate(36.1, -5.3, 150.0, T0, 3, "ASTRA", 95.0, 900.0, None, "sanctioned_owner")
    _, message, priority, _ = scene_notification(SceneReport("S1C_IW_GRDH_TEST", T0, 0.4, 3, 1, 1, 0, [managed]))
    assert "Se calló: ASTRA ⚠ ARMADOR O GESTOR SANCIONADO" in message and priority == "high"
    watched = Candidate(36.1, -5.3, 150.0, T0, 4, "FENOR", 95.0, 900.0, None, "watchlisted")
    _, message, priority, _ = scene_notification(SceneReport("S1C_IW_GRDH_TEST", T0, 0.4, 3, 1, 1, 0, [watched]))
    assert "Se calló: FENOR ⚠ EN LISTAS DE VIGILANCIA" in message and priority == "default"


def test_a_pass_in_two_frames_is_one_notification():
    from uatu.sar.report import scene_notification
    from uatu.sar.store import SceneReport

    report = SceneReport("S1C_IW_GRDH_TEST", T0, 0.6, 40, 13, 22, 3, [], frames=2)
    title, message, _, _ = scene_notification(report)
    assert title == "Pasada S1C 13/10 18:25 UTC: sin barcos oscuros"
    assert message.startswith(
        "40 barcos detectados (60% de la zona, 2 tomas): 13 con AIS, 22 sin AIS donde se recibe mal"
    )
    title, _, _, _ = scene_notification(report, late=True)
    assert title == "Pasada S1C 13/10 18:25 UTC (toma tardía): sin barcos oscuros"


def test_a_ferry_slowing_into_port_is_paired_on_its_fastest_speed():
    """CEUTA JET, 2026-10-14: 29 kn at 06:16:48, 7 kn entering Ceuta at 06:30:10, seen at 06:26:55."""
    from datetime import timedelta as td

    t0 = datetime(2026, 10, 14, 6, 16, 48, tzinfo=UTC)
    reports = [Report(t0, -5.3492, 35.9796, 29.4, 156), Report(t0 + td(seconds=802), -5.3115, 35.8948, 7.3, 243)]
    estimate = predict(reports, t0 + td(seconds=607))
    assert estimate.max_sog == 29.4 and estimate.sog == 7.3
    vessel = AisVessel(212561000, "CEUTA JET", 60, 60, estimate.lon, estimate.lat, estimate.sog, estimate.cog,
                       t0, estimate.gap_s, estimate.max_sog)  # fmt: skip
    [e] = expected_positions([vessel], geometry(heading_deg=193), GRID)
    assert e.tolerance_m > 1600  # 1 km + a quarter of 195 s at 29 kn, not at 7 kn


def test_overlay_lands_where_web_maps_put_it(tmp_path):
    """Web maps stretch an overlay linearly in Web Mercator between its corners: a bright pixel must
    come out where its true lon/lat is, not hundreds of metres off as a UTM image would."""
    grid = Grid.covering(bbox_polygon(STRAIT_OF_GIBRALTAR), 10.0).coarsened(8)
    grey = np.zeros(grid.shape, dtype=np.float32)
    row, col = int(grid.height * 0.8), int(grid.width * 0.9)  # far from the zone's central meridian
    grey[row, col] = 255
    lon, lat = grid.to_lonlat(*grid.to_map(np.array([row]), np.array([col])))
    write_overlay(grey, grid, tmp_path / "o.png", tmp_path / "o.json")

    (west, north), (east, north2), (east2, south), (west2, south2) = json.loads((tmp_path / "o.json").read_text())
    assert (north, east, south, west) == (north2, east2, south2, west2)  # north-up rectangle

    def mercator_y(lat_deg):
        return math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))

    image = np.asarray(Image.open(tmp_path / "o.png"))[..., 0]
    peak_row, peak_col = np.unravel_index(np.argmax(image), image.shape)
    height, width = image.shape
    map_lon = west + (peak_col + 0.5) / width * (east - west)
    map_lat_y = mercator_y(north) + (peak_row + 0.5) / height * (mercator_y(south) - mercator_y(north))
    map_lat = math.degrees(2 * math.atan(math.exp(map_lat_y)) - math.pi / 2)
    metres = math.hypot((map_lon - lon[0]) * 111_320 * math.cos(math.radians(lat[0])), (map_lat - lat[0]) * 110_574)
    assert metres < 80  # within one pixel


def test_a_frame_is_worth_processing_for_the_sea_it_covers_not_the_land():
    from shapely import wkt

    from uatu.config import ZONES
    from uatu.zones import sea_of

    catalonia = ZONES["cataluna"]
    sea = sea_of(catalonia)
    assert 0.35 < sea.area / bbox_polygon(catalonia.bbox).area < 0.5  # half the box is Catalonia itself
    # A frame of 19 July: a third of the zone's box, nearly all of it inland Catalonia and the Pyrenees.
    inland = wkt.loads("POLYGON((2.7414 41.4944,3.1398 42.9945,-0.0919 43.4055,-0.4139 41.9061,2.7414 41.4944))")
    assert inland.intersection(bbox_polygon(catalonia.bbox)).area / bbox_polygon(catalonia.bbox).area > 0.3
    assert inland.intersection(sea).area / sea.area < 0.1


def test_the_radar_image_is_cut_into_map_tiles_where_it_has_data(tmp_path):
    import io
    import math

    import rasterio
    from PIL import Image

    from uatu.sar.raster import backscatter_grey
    from uatu.sar.report import radar_image
    from uatu.web.passes.service import radar_tile

    # A small scene on a UTM grid at 20 m: bright sea on the left half, no data on the right half.
    grid = Grid(CRS.from_epsg(32630), Affine(20, 0, 280_000, 0, -20, 4_000_000), 2000, 1000)
    sigma0 = np.full(grid.shape, 0.05, dtype=np.float32)
    sigma0[:, 1000:] = np.nan
    utm = tmp_path / "radar_utm.tif"
    with rasterio.open(utm, "w", **grid.profile(dtype="uint8", nodata=0)) as dst:
        dst.write(backscatter_grey(sigma0), 1)
    radar_image(utm, tmp_path / "radar.tif")

    with rasterio.open(tmp_path / "radar.tif") as src:
        assert src.crs.to_epsg() == 3857 and src.overviews(1)
        bounds = src.bounds

    def tile_at(x_m: float, y_m: float, z: int) -> tuple[int, int]:
        size = 2 * math.pi * 6378137.0 / 2**z
        return int((x_m + math.pi * 6378137.0) / size), int((math.pi * 6378137.0 - y_m) / size)

    middle_y = (bounds.bottom + bounds.top) / 2
    left = tile_at(bounds.left + (bounds.right - bounds.left) * 0.2, middle_y, 13)
    right = tile_at(bounds.left + (bounds.right - bounds.left) * 0.8, middle_y, 13)
    image = Image.open(io.BytesIO(radar_tile(tmp_path / "radar.tif", 13, *left)))
    grey, *_, alpha = (np.asarray(band) for band in image.split())  # a grey image comes out as RGB, R = G = B
    assert image.size == (256, 256) and image.mode == "RGBA" and alpha.max() == 255
    assert abs(int(np.median(grey[alpha == 255])) - int(backscatter_grey(np.array([0.05]))[0])) <= 3
    assert radar_tile(tmp_path / "radar.tif", 13, *right) is None  # no data there
    assert radar_tile(tmp_path / "radar.tif", 13, 0, 0) is None  # far away

    # From afar, from an overview: a tile the image fills only in part has that part where it belongs.
    z, half = 9, math.pi * 6378137.0
    tx, ty = tile_at(bounds.left, bounds.top, z)
    size = 2 * half / 2**z
    expected_cols = np.clip(
        [(x - (tx * size - half)) / size * 256 for x in (bounds.left, (bounds.left + bounds.right) / 2)], 0, 256
    )
    expected_rows = np.clip([(half - y - ty * size) / size * 256 for y in (bounds.top, bounds.bottom)], 0, 256)
    alpha = np.asarray(Image.open(io.BytesIO(radar_tile(tmp_path / "radar.tif", z, tx, ty))).split()[-1])
    cols, rows = np.nonzero(alpha.any(axis=0))[0], np.nonzero(alpha.any(axis=1))[0]
    assert np.allclose([cols[0], cols[-1] + 1], expected_cols, atol=3)  # the left half has data
    assert np.allclose([rows[0], rows[-1] + 1], expected_rows, atol=3)


def test_the_recent_radar_shows_each_place_from_the_newest_pass_over_it(tmp_path):
    import io
    import math

    import rasterio
    from PIL import Image

    from uatu.sar.raster import backscatter_grey
    from uatu.sar.report import radar_image
    from uatu.web.radar.service import mosaic_tile

    grid = Grid(CRS.from_epsg(32630), Affine(20, 0, 280_000, 0, -20, 4_000_000), 2000, 1000)

    def frame(name: str, sigma0: np.ndarray):
        utm = tmp_path / f"{name}_utm.tif"
        with rasterio.open(utm, "w", **grid.profile(dtype="uint8", nodata=0)) as dst:
            dst.write(backscatter_grey(sigma0), 1)
        radar_image(utm, tmp_path / f"{name}.tif")
        return tmp_path / f"{name}.tif"

    older = frame("older", np.full(grid.shape, 0.01, dtype=np.float32))  # the whole area, darker
    newer_sigma0 = np.full(grid.shape, 0.1, dtype=np.float32)
    newer_sigma0[:, 1000:] = np.nan  # the western half only, brighter
    newer = frame("newer", newer_sigma0)

    with rasterio.open(older) as src:
        b = src.bounds
    size = 2 * math.pi * 6378137.0 / 2**13  # ~4 km: well inside the 40 x 20 km area
    x = int(((b.left + b.right) / 2 + math.pi * 6378137.0) / size)  # a tile across the middle
    y = int((math.pi * 6378137.0 - (b.bottom + b.top) / 2) / size)
    grey, *_, alpha = (np.asarray(b) for b in Image.open(io.BytesIO(mosaic_tile([newer, older], 13, x, y))).split())
    assert alpha.all()  # the older pass fills what the newer one did not image
    levels = set(np.unique(grey[alpha == 255]).tolist())
    bright, dark = (int(backscatter_grey(np.array([v]))[0]) for v in (0.1, 0.01))
    assert any(abs(v - bright) <= 3 for v in levels)  # newer on top where it has data
    assert any(abs(v - dark) <= 3 for v in levels)  # older elsewhere


def test_the_radar_colours_make_ships_stand_out_from_the_sea():
    from uatu.sar.raster import despeckle, radar_colour

    rng = np.random.default_rng(0)
    # Speckled calm sea (VV -20 dB, VH -28 dB; ~4.4 looks, like a GRD product) with a bright ship in the middle.
    vv = rng.gamma(4.4, 0.01 / 4.4, (64, 64)).astype(np.float32)
    vh = rng.gamma(4.4, 0.0016 / 4.4, (64, 64)).astype(np.float32)
    vv[30:34, 30:34], vh[30:34, 30:34] = 10.0, 1.0
    smooth = despeckle(vv)
    sea = np.s_[2:20, 2:20]
    assert smooth[sea].std() < vv[sea].std() / 2  # speckle smoothed over uniform sea...
    assert smooth[31, 31] > 1.0  # ...but the ship kept bright
    rgb = radar_colour(despeckle(vv), despeckle(vh))
    red, green, blue = (int(c) for c in rgb[:, 31, 31])
    assert min(red, green, blue) > 200  # the ship is near white
    red, green, blue = (int(c) for c in rgb[:, 10, 10])
    assert blue > red and max(red, green, blue) < 90  # the sea is dark navy
    nodata = radar_colour(np.full((2, 2), np.nan, np.float32), np.full((2, 2), np.nan, np.float32))
    assert not nodata.any()  # no data: 0, transparent
