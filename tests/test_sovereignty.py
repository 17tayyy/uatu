from datetime import UTC, date, datetime

import numpy as np
from shapely import box
from shapely.geometry import mapping

from uatu.ais.events import Track
from uatu.ais.reference import maritime_area_rows
from uatu.ais.sovereignty import (
    Waters,
    detect_state_vessels,
    downgrade_routine,
    flag,
    foreign_state_vessels,
    state_kind,
)

T0 = datetime(2026, 10, 19, 6, 0, tzinfo=UTC).timestamp()
# Spain's territorial sea around a city on another coast, and the waters close to it both states claim.
TERRITORIAL = Waters(49033, "Mar territorial español", "territorial_sea", ("Spain",), box(-5.6, 35.7, -5.0, 36.1))
CEUTA = Waters(49002, "Aguas de Ceuta", "disputed", ("Spain", "Morocco"), box(-5.39, 35.82, -5.28, 35.92))
MOROCCAN_PATROL, BRITISH_WARSHIP, SPANISH_PATROL = 242000001, 232035463, 224062000


def track(mmsi: int, lons, lats, sogs, minutes) -> Track:
    n = len(minutes)
    return Track(mmsi, T0 + np.asarray(minutes, float) * 60, np.asarray(lats, float), np.asarray(lons, float),
                 np.asarray(sogs, float), np.full(n, 90.0))  # fmt: skip


def test_state_vessels_by_type_or_name_and_their_flags():
    assert (flag(MOROCCAN_PATROL), flag(BRITISH_WARSHIP), flag(236112687)) == ("Marruecos", "Reino Unido", "Gibraltar")
    assert flag(970123456) is None  # a beacon's MMSI flies no flag
    assert state_kind("HMS CUTLASS", 35) == "military" and state_kind("SIR EVAN GIBB", 55) == "law_enforcement"
    assert state_kind("MARINE ROYALE 345", None) == "military" and state_kind("GARDE COTES 12", 37) == "law_enforcement"
    assert state_kind("MSC JOSSELINE", 71) is None and state_kind("SEA POLICEMAN", None) is None
    vessels = [(BRITISH_WARSHIP, "HMS CUTLASS", 35), (SPANISH_PATROL, "SPANISH NAVY P45", 35), (247000001, "MSC X", 70)]
    assert list(foreign_state_vessels(vessels)) == [BRITISH_WARSHIP]  # not Spain's own, not a container ship


def test_a_stay_in_claimed_waters_is_notable_passing_through_spains_is_not():
    state = foreign_state_vessels([(MOROCCAN_PATROL, "GARDE COTES 12", None), (SPANISH_PATROL, "SPANISH NAVY P45", 35)])
    # Through Spain's territorial sea at 15 kn (from the west), then 45 minutes nearly still off Ceuta.
    lons, lats = [-5.55, -5.5, -5.45, -5.35, -5.34, -5.34, -5.34, -5.34], [35.95] * 3 + [35.86] * 5
    passing = track(MOROCCAN_PATROL, lons, lats, [15, 15, 15, 1, 1, 0.5, 1, 1], [0, 5, 10, 20, 35, 50, 60, 65])
    own = track(SPANISH_PATROL, [-5.34] * 4, [35.86] * 4, [0] * 4, [0, 20, 40, 60])
    events = detect_state_vessels([passing, own], state, [TERRITORIAL, CEUTA])
    assert [e.mmsi for e in events] == [MOROCCAN_PATROL, MOROCCAN_PATROL]  # Spain's own are not told
    through, off_ceuta = sorted(events, key=lambda e: e.start)
    assert (through.details["waters"], through.severity, through.details["minutes"]) == (
        "Mar territorial español",
        1,
        10,
    )
    assert through.details["mean_sog"] == 15
    assert (off_ceuta.details["waters"], off_ceuta.details["waters_kind"], off_ceuta.severity) == (
        "Aguas de Ceuta", "disputed", 2
    )  # fmt: skip
    assert off_ceuta.details["claimants"] == ["Spain", "Morocco"] and off_ceuta.details["slow_minutes"] == 45
    assert off_ceuta.details["flag"] == "Marruecos" and off_ceuta.details["vessel_kind"] == "law_enforcement"


def test_nearly_still_in_spains_own_waters_is_notable_and_a_glimpse_is_nothing():
    state = foreign_state_vessels([(BRITISH_WARSHIP, "HMS CUTLASS", 35)])
    still = track(BRITISH_WARSHIP, [-5.2] * 5, [35.75] * 5, [1, 1, 1, 1, 1], [0, 10, 20, 30, 40])
    [e] = detect_state_vessels([still], state, [TERRITORIAL, CEUTA])
    assert (e.severity, e.details["slow_minutes"]) == (2, 40)
    glimpse = track(BRITISH_WARSHIP, [-5.2, -5.2], [35.75, 35.75], [12, 12], [0, 5])
    assert detect_state_vessels([glimpse], state, [TERRITORIAL]) == []


def test_berthed_at_its_base_is_no_stay():
    """Gibraltar's police launches, moored in Gibraltar's harbour, inside the waters Spain claims too."""
    state = foreign_state_vessels([(236112687, "SENTINEL", 55)])
    moored = track(236112687, [-5.34] * 5, [35.86] * 5, [0] * 5, [0, 15, 30, 45, 60])
    at_quay = lambda lon, lat: np.full(np.shape(lon), 80.0)  # noqa: E731 - 80 m from land
    assert detect_state_vessels([moored], state, [CEUTA], at_quay) == []
    assert len(detect_state_vessels([moored], state, [CEUTA], lambda lon, lat: np.full(np.shape(lon), 3000.0))) == 1


def test_what_a_vessel_does_every_day_is_routine():
    state = foreign_state_vessels([(BRITISH_WARSHIP, "HMS CUTLASS", 35)])
    [e] = detect_state_vessels(
        [track(BRITISH_WARSHIP, [-5.34] * 4, [35.86] * 4, [5] * 4, [0, 10, 20, 30])], state, [CEUTA]
    )
    assert e.severity == 2
    days = {(BRITISH_WARSHIP, CEUTA.id): {date(2026, 10, d) for d in (15, 16, 17, 19)}}  # the 19th is today's own
    [routine] = downgrade_routine([e], days)
    assert (routine.severity, routine.details["routine_days"]) == (1, 3)
    assert downgrade_routine([e], {(BRITISH_WARSHIP, CEUTA.id): {date(2026, 10, 17)}}) == [e]


def test_spains_waters_from_marine_regions():
    claims = {"features": [{"properties": {"mrgid": 49002, "geoname": "Overlapping claim Ceuta: Spain / Morocco",
                                           "sovereign1": "Spain", "sovereign2": "Morocco"},
                            "geometry": mapping(CEUTA.geometry)}]}  # fmt: skip
    territorial = {"features": [{"properties": {"mrgid": 49087, "geoname": "Spanish 12 NM (Canary Islands)",
                                                "sovereign1": "Spain"},
                                 "geometry": mapping(box(-18, 27.5, -13.3, 29.5))}]}  # fmt: skip
    ceuta, canaries = maritime_area_rows(claims, territorial)
    assert (ceuta["name"], ceuta["kind"], ceuta["claimants"]) == ("Aguas de Ceuta", "disputed", ["Spain", "Morocco"])
    assert (canaries["name"], canaries["kind"]) == ("Mar territorial español (Canarias)", "territorial_sea")
    assert canaries["geom"].startswith("SRID=4326;MULTIPOLYGON")
