"""Stops and Strait transits from AIS tracks."""

from datetime import UTC, datetime

import numpy as np

from uatu.ais.events import LocalProjection, Track
from uatu.ais.stops import Port, Ports, detect_stops, detect_transits

T0 = datetime(2026, 10, 14, 0, 0, tzinfo=UTC).timestamp()
PROJ = LocalProjection(-5.4, 36.0)
CEUTA = Port(1, "Ceuta", "Spain", -5.3167, 35.9)
PORTS = Ports([CEUTA, Port(2, "Algeciras", "Spain", -5.4333, 36.1333)])


def track(minutes, lon, lat, sog, nav=None, mmsi: int = 1) -> Track:
    minutes = np.asarray(minutes, dtype=float)
    n = len(minutes)

    def full(v):
        return np.broadcast_to(np.asarray(v, dtype=float), (n,)).copy()

    return Track(
        mmsi, T0 + minutes * 60, full(lat), full(lon), full(sog), np.full(n, 90.0), None if nav is None else full(nav)
    )


def coast(km: float):
    return lambda lon, lat: np.full(np.shape(lon), km * 1000.0)


def stops(tracks, coast_km: float):
    return detect_stops(tracks, coast(coast_km), PORTS, PROJ)


def test_a_vessel_at_anchor_off_ceuta_heard_once_an_hour():
    hours = np.arange(0, 11) * 60
    jitter = np.sin(np.arange(11)) * 0.002  # swinging around its anchor, ~200 m
    anchored = track(hours, -5.30 + jitter, 35.93, 0.2, nav=1)
    [stop] = stops([anchored], coast_km=3)
    assert (stop.kind, stop.place.name, stop.hours) == ("anchorage", "Ceuta", 10.0)
    assert 3_000 < stop.place_m < 4_000


def test_alongside_by_its_status_or_by_a_port_and_a_coast():
    minutes = range(0, 181, 10)
    assert stops([track(minutes, -5.3167, 35.895, 0.0, nav=5)], coast_km=0.2)[0].kind == "berth"
    # A yacht with no navigational status: by the coast and a port, at a berth...
    assert stops([track(minutes, -5.32, 35.89, 0.0)], coast_km=0.3)[0].kind == "berth"
    # ...by the coast far from any port, anchored in a cove.
    [cove] = stops([track(minutes, -5.25, 36.25, 0.0)], coast_km=0.3)  # ~21 km from Algeciras
    assert cove.kind == "anchorage" and cove.place is not None and cove.place.name == "Algeciras"
    # Far out, lying still at sea, nameless.
    [still] = stops([track(minutes, -3.0, 36.0, 0.3)], coast_km=40)
    assert (still.kind, still.place) == ("drifting", None)


def test_stops_end_when_it_moves_and_survive_silences_where_it_stays():
    # Stopped 2 h, then under way: one stop, of 2 h.
    minutes = [*range(0, 121, 10), 130, 140]
    lon = [-5.30] * 13 + [-5.28, -5.26]
    [stop] = stops([track(minutes, lon, 35.93, [0.1] * 13 + [9.0, 9.0], nav=1)], coast_km=3)
    assert stop.hours == 2.0
    # Unheard for 5 h but found in place: the same stop. For 7 h: two.
    assert len(stops([track([0, 60, 360, 420], -5.30, 35.93, 0.1, nav=1)], coast_km=3)) == 1
    assert len(stops([track([0, 60, 480, 540], -5.30, 35.93, 0.1, nav=1)], coast_km=3)) == 2
    # Half an hour is not a stop.
    assert stops([track(range(0, 31, 5), -5.30, 35.93, 0.1, nav=1)], coast_km=3) == []


def test_drifting_is_one_stop_moving_on_to_another_is_two():
    # Waiting off Tanger Med for a berth, drifting east at 0.6 kn for 6 h: ~6.7 km, one stop.
    minutes = np.arange(0, 361, 20)
    lon = -5.45 + minutes / 60 * 0.6 * 1852 / (111_320 * np.cos(np.radians(35.95)))
    [drift] = stops([track(minutes, lon, 35.95, 0.6)], coast_km=6)
    assert (drift.kind, drift.hours) == ("anchorage", 6.0)
    # Stopped at one spot, unheard for an hour, then stopped 8 km away: it sailed there. Two stops.
    minutes = [*range(0, 121, 20), *range(180, 301, 20)]
    lon = [-5.30] * 7 + [-5.21] * 7
    assert len(stops([track(minutes, lon, 35.93, 0.1, nav=1)], coast_km=3)) == 2


def test_transits_of_the_narrows_but_not_the_ferries_or_the_manoeuvres_beside_them():
    eastbound = track([0, 20, 40], [-5.70, -5.60, -5.35], [35.95, 35.95, 35.96], 14, mmsi=1)
    westbound = track([0, 20], [-5.40, -5.70], [35.97, 35.96], 14, mmsi=2)
    tarifa_tangier = track([0, 20, 40], [-5.60, -5.70, -5.80], [36.0, 35.9, 35.79], 20, mmsi=3)
    algeciras_tanger_med = track([0, 30, 60], [-5.43, -5.47, -5.50], [36.13, 36.0, 35.89], 20, mmsi=4)
    unheard = track([0, 240], [-5.70, -5.40], [35.95, 35.96], 14, mmsi=5)  # where it crossed, unknown
    # Off Tanger Med from the east, turning west of the meridian to come alongside: no passage.
    manoeuvre = track([0, 30, 60, 90], [-5.35, -5.53, -5.57, -5.51], 35.9, 5, mmsi=6)
    # Crossed, but not seen beyond the far side yet: a later analysis will count it.
    not_yet = track([0, 20], [-5.70, -5.52], 35.95, 14, mmsi=7)
    found = detect_transits([eastbound, westbound, tarifa_tangier, algeciras_tanger_med, unheard, manoeuvre, not_yet])
    assert [(t.mmsi, t.direction) for t in found] == [(1, "east"), (2, "west")]
    assert found[0].at.timestamp() == T0 + (20 + 0.2 * 20) * 60  # 0.05 of 0.25 degrees past -5.60
