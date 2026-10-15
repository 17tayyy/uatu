"""AIS that cannot be true: MMSIs that are no ship's, positions inland, two transmitters on one identity."""

from datetime import UTC, datetime

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform

from uatu.ais import mmsi
from uatu.ais.coast import CoastDistance
from uatu.ais.events import LocalProjection, Track
from uatu.ais.spoofing import detect_odd_mmsi, detect_on_land, detect_shared_mmsi
from uatu.sar.raster import Grid

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC).timestamp()
PROJ = LocalProjection(-5.5, 36.0)
LAT0, LON0 = 36.0, -5.6
SPANISH = 224_123_456


def track(mmsi: int, minutes, lon=LON0, sog=10.0) -> Track:
    minutes = np.asarray(minutes, dtype=float)
    n = len(minutes)
    lons = np.broadcast_to(np.asarray(lon, dtype=float), (n,)).copy()
    return Track(mmsi, T0 + minutes * 60, np.full(n, LAT0), lons, np.full(n, float(sog)), np.full(n, 90.0))


def at_sea(km: float):
    return lambda lon, lat: np.full(np.shape(lon), km * 1000.0)


def test_what_an_mmsi_says_it_is():
    kinds = {
        SPANISH: "ship",
        982_241_234: "craft",  # a parent ship's boat
        992_241_234: "aton",
        2_241_234: "coast_station",  # 002241234
        22_412_345: "group",  # 022412345
        822_412_345: "handheld",
        111_224_123: "sar_aircraft",
        970_123_456: "sart",
        972_123_456: "mob",
        974_123_456: "epirb",
    }
    assert {m: mmsi.kind(m) for m in kinds} == kinds
    # A flag no country has, too few digits, placeholders, nothing at all.
    for odd in (365_123_456, 17_620, 123_456_789, 111_111_111, 0, 1_000_000_000):
        assert mmsi.kind(odd) == "invalid", odd


def test_a_ship_reported_far_inland_again_and_again():
    inland_3km = lambda lon, lat: np.full(np.shape(lon), 3_000.0)  # noqa: E731
    [event] = detect_on_land([track(SPANISH, [0, 1, 2])], inland_3km)
    assert (event.kind, event.severity, event.details) == ("position_on_land", 1, {"reports": 3, "inland_km": 3.0})
    [far] = detect_on_land([track(SPANISH, [0, 1, 2])], at_sea(12))  # 12 km inland: no canal goes that far
    assert (far.severity, far.details["inland_km"]) == (2, 12.0)
    assert detect_on_land([track(SPANISH, [0, 1])], inland_3km) == []  # twice is a glitch
    assert detect_on_land([track(SPANISH, [0, 1, 2])], at_sea(0)) == []  # 0 m inland: at sea
    assert detect_on_land([track(970_123_456, [0, 1, 2])], inland_3km) == []  # a beacon carried ashore


def test_one_mmsi_in_two_places_at_once():
    there_and_back = [LON0, LON0 + 0.25] * 3  # ~22 km apart, a minute between reports
    [event] = detect_shared_mmsi([track(SPANISH, range(6), lon=there_and_back)], PROJ)
    assert (event.kind, event.severity, event.details["jumps"]) == ("shared_mmsi", 2, 5)
    assert 21 < event.details["apart_km"] < 24
    one_jump = [LON0, LON0, LON0, LON0 + 0.25, LON0 + 0.25, LON0 + 0.25]  # a bad fix, or a corrupted message
    assert detect_shared_mmsi([track(SPANISH, range(6), lon=one_jump)], PROJ) == []
    sailing = np.linspace(LON0, LON0 + 0.05, 6)  # 10 knots or so
    assert detect_shared_mmsi([track(SPANISH, np.arange(6) * 5, lon=sailing)], PROJ) == []


def test_mmsis_no_ship_should_have_and_emergency_beacons():
    coast_station, beacon = 2_241_234, 970_123_456
    [odd] = detect_odd_mmsi([track(coast_station, [0, 10], sog=12)], at_sea(10))
    assert (odd.kind, odd.severity, odd.details) == (
        "odd_mmsi", 2, {"mmsi_kind": "coast_station", "at_sea": True, "under_way": True},
    )  # fmt: skip
    [moored] = detect_odd_mmsi([track(365_123_456, [0, 10], sog=0)], at_sea(0.2))
    assert (moored.severity, moored.details["mmsi_kind"]) == (1, "invalid")  # set up wrong, in port
    [sart] = detect_odd_mmsi([track(beacon, [0, 1], sog=0)], at_sea(10))
    assert (sart.kind, sart.severity, sart.details) == ("emergency_beacon", 2, {"beacon": "sart", "at_sea": True})
    assert detect_odd_mmsi([track(beacon, [0, 1], sog=0)], at_sea(0.5))[0].severity == 1  # tested in port
    assert detect_odd_mmsi([track(SPANISH, [0, 1]), track(982_241_234, [0, 1])], at_sea(10)) == []


def test_inland_is_measured_to_the_nearest_water_harbours_staying_water(tmp_path):
    # 10 m pixels: sea on the left, land from 2 km east with a harbour basin 60 m wide at 3 km;
    # a 30 m breakwater out at sea.
    grid = Grid(CRS.from_epsg(32630), Affine(10, 0, 280_000, 0, -10, 3_990_000), 800, 400)
    land = np.zeros(grid.shape, dtype=np.uint8)
    land[:, 200:] = 1
    land[150:250, 300:306] = 0
    land[:, 50:53] = 1
    path = tmp_path / "land.tif"
    with rasterio.open(path, "w", **grid.profile(dtype="uint8")) as dst:
        dst.write(land, 1)
    coast = CoastDistance(grid, path, tmp_path / "cache")

    def lonlat(x: float) -> tuple[np.ndarray, np.ndarray]:
        lons, lats = transform(grid.crs, "EPSG:4326", [280_000 + x], [3_990_000 - 2_000])
        return np.array(lons), np.array(lats)

    assert coast.inland_at(*lonlat(1_000))[0] == 0  # at sea
    assert coast.inland_at(*lonlat(530))[0] == 0  # on the breakwater: water, at 100 m
    assert coast.inland_at(*lonlat(3_030))[0] == 0  # in the harbour
    assert coast.inland_at(*lonlat(2_550))[0] == 500
    assert coast.inland_at(*lonlat(7_050))[0] == 4_000  # to the harbour, nearer than the open sea
    assert (tmp_path / "cache" / f"inland_distance_{coast.grid.key}.tif").exists()
    again = CoastDistance(grid, path, tmp_path / "cache")  # from the cache
    assert again.inland_at(*lonlat(7_050))[0] == coast.inland_at(*lonlat(7_050))[0]
