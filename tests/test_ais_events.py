from datetime import UTC, datetime, timedelta

import numpy as np
from shapely import LineString

from uatu.ais.events import (
    LocalProjection,
    StaticReport,
    Track,
    detect_encounters,
    detect_gaps,
    detect_identity_changes,
    detect_infrastructure_loitering,
    detect_jumps,
    detect_loitering,
)
from uatu.config import STRAIT_OF_GIBRALTAR

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC).timestamp()
PROJ = LocalProjection(-5.5, 36.0)
LAT0, LON0 = 36.0, -5.6  # mid-strait, well inside the area


def track(mmsi: int, minutes, lat=LAT0, lon=LON0, sog=10.0) -> Track:
    minutes = np.asarray(minutes, dtype=float)
    n = len(minutes)
    return Track(
        mmsi,
        T0 + minutes * 60,
        np.broadcast_to(np.asarray(lat, dtype=float), (n,)).copy(),
        np.broadcast_to(np.asarray(lon, dtype=float), (n,)).copy(),
        np.broadcast_to(np.asarray(sog, dtype=float), (n,)).copy(),
        np.full(n, 90.0),
    )


def at_sea(km: float):
    return lambda lon, lat: np.full(np.shape(lon), km * 1000.0)


def gaps(tracks, minutes_now: int, coast_km: float = 10):
    return detect_gaps(tracks, T0 + minutes_now * 60, STRAIT_OF_GIBRALTAR, at_sea(coast_km), PROJ)


def meet(*tracks, coast_km: float = 20):
    return detect_encounters(list(tracks), T0, T0 + 180 * 60, at_sea(coast_km), PROJ)


def neighbours(count: int, minutes, offset_deg: float = 0.02) -> list[Track]:
    """Vessels heard around LAT0/LON0 (2 km away) the whole time."""
    return [track(900 + k, minutes, LAT0 + offset_deg, LON0 + 0.001 * k) for k in range(count)]


# --- gaps ------------------------------------------------------------------------------------------


def ahead(count: int, minutes) -> list[Track]:
    """Vessels heard where a 10-knot eastbound vessel from LON0 is an hour later (~18.5 km east)."""
    return [track(950 + k, minutes, LAT0 + 0.01, LON0 + 0.2 + 0.001 * k) for k in range(count)]


def test_a_vessel_going_silent_where_others_are_heard_is_a_gap():
    silent = track(1, range(0, 61))  # 10 kn eastbound, reports for an hour, then nothing
    now = T0 + 180 * 60
    others = [*neighbours(4, range(0, 181)), *ahead(3, range(0, 181))]
    [gap] = detect_gaps([silent, *others], now, STRAIT_OF_GIBRALTAR, at_sea(10), PROJ)
    assert (gap.kind, gap.mmsi, gap.severity, gap.end) == ("ais_gap", 1, 2, None)
    assert gap.start.timestamp() == T0 + 60 * 60
    assert gap.details["ais_heard_nearby"] == 4 and gap.details["ais_heard_ahead"] == 3
    assert gap.details["silent_minutes"] == 120


def test_a_gap_when_uatu_itself_went_deaf_says_nothing():
    """CHEM NICHOLAS, 13 October: last heard at 21:55 UTC off Tarifa; Uatu's connection dropped at 22:34."""
    from datetime import timedelta

    from uatu.ais.events import downgrade_when_blind

    silent = track(1, range(0, 61))
    [gap] = detect_gaps([silent, *neighbours(4, range(0, 181)), *ahead(3, range(0, 181))], T0 + 180 * 60,
                        STRAIT_OF_GIBRALTAR, at_sea(10), PROJ)  # fmt: skip
    assert gap.severity == 2
    outage = (gap.start + timedelta(minutes=38), gap.start + timedelta(hours=3, minutes=24))
    [blind] = downgrade_when_blind([gap], [outage])
    assert blind.severity == 1 and blind.details["uatu_blind"]
    earlier = (gap.start - timedelta(hours=3), gap.start - timedelta(hours=1))
    assert downgrade_when_blind([gap], [earlier]) == [gap]  # deaf before it went quiet: no matter


def test_sailing_into_a_coverage_hole_is_not_going_dark():
    silent = track(1, range(0, 61))
    [gap] = gaps([silent, *neighbours(4, range(0, 181))], minutes_now=180)  # nobody heard ahead
    assert gap.severity == 1 and gap.details["ais_heard_ahead"] == 0


def test_no_gap_without_coverage_near_the_edge_or_in_port():
    silent = track(1, range(0, 61))
    now = T0 + 180 * 60
    assert detect_gaps([silent], now, STRAIT_OF_GIBRALTAR, at_sea(10), PROJ) == []  # nobody else heard
    edge = track(1, range(0, 61), lat=35.55, lon=-5.6)  # 5 km from the area's southern edge
    others = neighbours(4, range(0, 181), offset_deg=-0.44)
    assert detect_gaps([edge, *others], now, STRAIT_OF_GIBRALTAR, at_sea(10), PROJ) == []
    # In a harbour vessels go quiet all the time: not kept at all. A little further out, kept.
    assert detect_gaps([silent, *neighbours(4, range(0, 181))], now, STRAIT_OF_GIBRALTAR, at_sea(1), PROJ) == []
    [inshore] = detect_gaps([silent, *neighbours(4, range(0, 181))], now, STRAIT_OF_GIBRALTAR, at_sea(3), PROJ)
    assert inshore.mmsi == 1 and inshore.details["coast_km"] == 3


def test_a_gap_that_ends_records_where_the_vessel_reappeared():
    minutes = [*range(0, 31), 120]
    lat = [LAT0] * 31 + [LAT0 + 0.1]  # ~11 km further north
    reappeared = track(1, minutes, lat=lat)
    [gap] = detect_gaps(
        [reappeared, *neighbours(3, range(0, 130))], T0 + 130 * 60, STRAIT_OF_GIBRALTAR, at_sea(10), PROJ
    )
    assert gap.end is not None and gap.end.timestamp() == T0 + 120 * 60
    assert gap.details["silent_minutes"] == 90 and 10 < gap.details["moved_km"] < 12


# --- encounters ------------------------------------------------------------------------------------


def test_two_stopped_vessels_side_by_side_for_hours_meet():
    minutes = range(0, 181, 3)
    a = track(1, minutes, sog=0.3)
    b = track(2, minutes, lat=LAT0 + 0.002, sog=0.5)  # ~220 m apart
    [e] = detect_encounters([a, b], T0, T0 + 180 * 60, at_sea(20), PROJ)
    assert (e.kind, e.mmsi, e.other_mmsi, e.severity) == ("encounter", 1, 2, 2)
    assert 2.5 <= e.details["hours"] <= 3 and e.details["min_distance_m"] < 250


def test_no_encounter_when_apart_moving_brief_or_in_port():
    minutes = range(0, 181, 3)
    a = track(1, minutes, sog=0.3)
    assert (
        detect_encounters([a, track(2, minutes, lat=LAT0 + 0.02, sog=0.3)], T0, T0 + 180 * 60, at_sea(20), PROJ) == []
    )
    assert meet(a, track(2, minutes, lat=LAT0 + 0.002, sog=8)) == []
    brief = track(2, range(0, 61, 3), lat=LAT0 + 0.002, sog=0.3)
    assert meet(a, brief) == []
    [bunkering] = detect_encounters(
        [a, track(2, minutes, lat=LAT0 + 0.002, sog=0.3)], T0, T0 + 180 * 60, at_sea(2), PROJ
    )
    assert bunkering.severity == 1  # near the coast: most likely routine
    moored = track(2, minutes, lat=LAT0 + 0.002, sog=0.0)
    assert meet(a, moored, coast_km=0.5) == []  # in a harbour: side by side for days, not a meeting


# --- loitering, infrastructure ---------------------------------------------------------------------


def test_loitering_offshore_but_not_at_anchor():
    stopped = track(1, range(0, 241, 2), sog=0.4)
    [e] = detect_loitering([stopped], at_sea(20))
    assert e.kind == "loitering" and e.details["hours"] == 4
    assert detect_loitering([stopped], at_sea(1)) == []  # an anchorage near the coast
    assert detect_loitering([track(1, range(0, 241, 2), sog=0.4)], at_sea(20))[0].severity == 2
    silent_hours = track(1, [*range(0, 61, 2), *range(200, 261, 2)], sog=0.4)
    assert detect_loitering([silent_hours], at_sea(20)) == []  # reports stopped in between


def test_slow_vessel_over_a_cable():
    x, y = PROJ.xy(np.array([LON0]), np.array([LAT0]))
    cable = LineString([(x[0] - 5000, y[0] + 300), (x[0] + 5000, y[0] + 300)])  # 300 m north of the vessel
    infrastructure = [("telecom_cable", "TEST-1", cable)]
    slow = track(1, range(0, 41), sog=1.0)
    [e] = detect_infrastructure_loitering([slow], infrastructure, at_sea(10), PROJ)
    assert (e.kind, e.severity, e.details["infrastructure"]) == ("infrastructure_loitering", 3, "TEST-1")
    assert 290 <= e.details["min_distance_m"] <= 310
    assert detect_infrastructure_loitering([track(1, range(0, 41), sog=12)], infrastructure, at_sea(10), PROJ) == []
    assert detect_infrastructure_loitering([track(1, range(0, 20), sog=1)], infrastructure, at_sea(10), PROJ) == []


# --- jumps, identity -------------------------------------------------------------------------------


def test_impossible_jump():
    lat = [LAT0, LAT0, LAT0 + 0.5, LAT0 + 0.5]  # 55 km in a minute
    [e] = detect_jumps([track(1, [0, 1, 2, 3], lat=lat)], PROJ)
    assert e.kind == "position_jump" and e.details["jump_km"] > 50
    assert detect_jumps([track(1, range(0, 10))], PROJ) == []


def test_identity_changes_only_on_different_known_values():
    t = datetime.fromtimestamp(T0, UTC)
    reports = [
        StaticReport(1, t - timedelta(days=2), "OLD NAME", 9876543, "ABC1", 80),
        StaticReport(1, t + timedelta(hours=1), "OLD NAME", None, None, None),  # partial report: nothing new
        StaticReport(1, t + timedelta(hours=2), "NEW NAME", 9876543, "ABC1", 80),
        StaticReport(2, t + timedelta(hours=2), "SAME", None, None, 70),
    ]
    [e] = detect_identity_changes(reports, since=t, where={1: (LAT0, LON0), 2: (LAT0, LON0)})
    assert (e.mmsi, e.severity, e.details) == (1, 2, {"field": "name", "before": "OLD NAME", "after": "NEW NAME"})


def test_vessels_sailing_out_or_into_port_are_not_hiding():
    others = neighbours(4, range(0, 181))
    east_edge = track(1, range(0, 61), lon=-4.75, sog=15)  # heading east at 15 kn: out of the area in < 1 h
    assert gaps([east_edge, *neighbours(4, range(0, 181), offset_deg=0.02)], minutes_now=180) == []

    def coast_east(lon, lat):  # land starts 5 km east of the vessel
        return np.where(np.asarray(lon) > LON0 + 0.055, 0.0, 10_000.0)

    to_port = track(1, range(0, 61), sog=10)  # 18 km in an hour, eastwards: reaches that coast
    [gap] = detect_gaps([to_port, *others], T0 + 180 * 60, STRAIT_OF_GIBRALTAR, coast_east, PROJ)
    assert gap.severity == 1 and gap.details["heading_to_port"]


def test_stopped_or_patchy_vessels_going_silent_are_informative():
    stopped = track(1, range(0, 61), sog=0.2)
    [gap] = gaps([stopped, *neighbours(4, range(0, 181))], minutes_now=180)
    assert gap.severity == 1
    patchy = track(1, [*range(0, 11), *range(80, 91)], sog=10)  # two silences of over an hour
    found = gaps([patchy, *neighbours(4, range(0, 181))], minutes_now=180)
    assert len(found) == 2 and all(g.severity == 1 and g.details["patchy_reception"] for g in found)


def test_events_among_many_stopped_vessels_are_routine():
    from uatu.ais.events import downgrade_in_crowds, stopped_points

    minutes = range(0, 241, 2)
    loiterer = track(1, minutes, sog=0.4)
    [alone] = downgrade_in_crowds(detect_loitering([loiterer], at_sea(20)), stopped_points([loiterer]), PROJ)
    assert alone.severity == 2
    anchorage = [track(100 + k, minutes, lat=LAT0 + 0.005 * k, sog=0.1) for k in range(6)]  # within ~3 km
    stopped = stopped_points([loiterer, *anchorage])
    [routine] = downgrade_in_crowds(detect_loitering([loiterer], at_sea(20)), stopped, PROJ)
    assert routine.severity == 1 and routine.details["vessels_stopped_nearby"] == 6


def test_waiting_areas_are_learnt_from_past_days_too():
    from uatu.ais.events import downgrade_in_crowds

    loiterer = track(1, range(0, 241, 2), sog=0.4)
    # Nobody else stopped there today, but five vessels waited around it earlier in the week.
    history = [(200 + k, LON0 + 0.02 * k, LAT0 + 0.01) for k in range(5)]
    [e] = downgrade_in_crowds(detect_loitering([loiterer], at_sea(20)), history, PROJ)
    assert e.severity == 1 and e.details["vessels_stopped_nearby"] == 5


def test_vessels_over_cables_matter_only_where_anchors_reach_the_seabed():
    x, y = PROJ.xy(np.array([LON0]), np.array([LAT0]))
    cable = LineString([(x[0] - 5000, y[0] + 300), (x[0] + 5000, y[0] + 300)])
    slow = track(1, range(0, 41), sog=1.0)

    def depth(metres: float):
        return lambda lon, lat: np.full(np.shape(lon), metres)

    [shallow] = detect_infrastructure_loitering(
        [slow], [("telecom_cable", "T", cable)], at_sea(10), PROJ, depth=depth(60)
    )
    [deep] = detect_infrastructure_loitering(
        [slow], [("telecom_cable", "T", cable)], at_sea(10), PROJ, depth=depth(865)
    )
    assert (shallow.severity, shallow.details["depth_m"]) == (3, 60)
    assert (deep.severity, deep.details["depth_m"]) == (1, 865)


def test_bathymetry_reads_depth_from_the_terrain_model(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from uatu.ais.bathymetry import Bathymetry

    elevation = np.array([[-30.0, -865.0], [12.0, -200.0]], dtype=np.float32)  # metres, negative = below sea
    path = tmp_path / "depth.tif"
    profile = {"driver": "GTiff", "width": 2, "height": 2, "count": 1, "dtype": "float32", "crs": "EPSG:4326",
               "transform": from_origin(-6.0, 36.0, 0.5, 0.5)}  # fmt: skip
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(elevation, 1)
    depth = Bathymetry(path).depth_at(np.array([-5.9, -5.4, -5.9, -7.0]), np.array([35.9, 35.9, 35.4, 35.9]))
    assert depth[:3].tolist() == [30.0, 865.0, -12.0] and np.isnan(depth[3])


def test_heading_where_vessels_are_heard_only_now_and_then_is_not_notable():
    silent = track(1, range(0, 61))  # 10 kn eastbound, then nothing
    glimpses = [track(950 + k, [k * 10], LAT0 + 0.01, LON0 + 0.2 + 0.001 * k) for k in range(5)]  # once each
    [gap] = [g for g in gaps([silent, *neighbours(4, range(0, 181)), *glimpses], minutes_now=180) if g.mmsi == 1]
    assert gap.severity == 1 and gap.details["poor_reception_ahead"]


def test_a_gap_is_told_as_far_as_uatu_knows():
    from datetime import UTC, datetime, timedelta

    from uatu.ais.analysis import describe
    from uatu.ais.events import Event

    now = datetime.now(UTC)
    details = {"silent_minutes": 90, "coast_km": 5.2, "ais_heard_nearby": 51}

    def told(start, end=None, names=None):
        return describe(Event("ais_gap", 2, 248001000, start, 35.97, -5.55, details, end), names or {})[1]

    assert told(now - timedelta(minutes=90)).startswith("MMSI 248001000 lleva 90 min sin emitir")
    assert "estuvo 90 min sin emitir" in told(now - timedelta(hours=3), now - timedelta(hours=1, minutes=30))
    gone = told(now - timedelta(days=2), names={248001000: "XT HOPE"})
    assert gone.startswith("XT HOPE (MMSI 248001000) dejó de oírse y no se le ha vuelto a oír")


def test_a_gap_where_many_go_quiet_is_the_receivers_not_the_vessel():
    from uatu.ais.events import downgrade_where_usually_silent

    silent = track(1, range(0, 61))
    [gap] = detect_gaps([silent, *neighbours(4, range(0, 181)), *ahead(3, range(0, 181))], T0 + 180 * 60,
                        STRAIT_OF_GIBRALTAR, at_sea(10), PROJ)  # fmt: skip
    # Five other vessels went quiet within 5 km lately (one twice), and the vessel itself before: a sink.
    quiet = [(10 + k, LON0 + 0.01 * k, LAT0) for k in range(5)] + [(10, LON0, LAT0), (1, LON0, LAT0)]
    [usual] = downgrade_where_usually_silent([gap], quiet, PROJ)
    assert usual.severity == 1 and usual.details["usual_silence"] == 5
    # Four is not enough, nor are five far away.
    assert downgrade_where_usually_silent([gap], quiet[:4], PROJ) == [gap]
    far = [(10 + k, LON0 + 0.2, LAT0 + 0.2) for k in range(5)]
    assert downgrade_where_usually_silent([gap], far, PROJ) == [gap]
