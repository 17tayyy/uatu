from shapely import LineString

from uatu.ais.reference import emodnet_features, sanctioned_vessels, telegeography_features
from uatu.ais.store import valid_imo


def row(**fields) -> dict[str, str]:
    base = dict.fromkeys(
        ["id", "schema", "name", "aliases", "identifiers", "countries", "program_ids", "sanctions"], ""
    )
    return base | fields


def test_sanctioned_vessels_are_keyed_by_imo():
    rows = [
        row(id="NK-1", schema="Vessel", name="Nurkez", identifiers="352002470;3E2311;IMO9253325", program_ids="EU-RUS"),
        row(id="NK-2", schema="Vessel", name="No IMO", identifiers="123456789"),
        row(id="NK-3", schema="Person", name="Someone", identifiers="IMO1234567"),
    ]
    [vessel] = sanctioned_vessels(rows)
    assert (vessel["imo"], vessel["name"], vessel["mmsis"], vessel["programs"]) == (
        9253325,
        "Nurkez",
        "352002470",
        "EU-RUS",
    )


def test_valid_imo_checks_the_check_digit():
    assert valid_imo(9253325) and valid_imo(9246322)
    assert valid_imo(1234567)  # 1*7 + 2*6 + 3*5 + 4*4 + 5*3 + 6*2 = 77: check digit 7
    assert not valid_imo(9253326) and not valid_imo(1234568) and not valid_imo(None) and not valid_imo(123)


def test_emodnet_cables_are_operational_or_abandoned():
    line = {"type": "MultiLineString", "coordinates": [[[-5.5, 36.0], [-5.4, 36.1]]]}
    collection = {"features": [
        {"geometry": line, "properties": {"name": "CABLES DE TELEFONO OPERATIVOS"}},
        {"geometry": line, "properties": {"name": "CABLES DE TELEFONO ABANDONADOS"}},
        {"geometry": None, "properties": {"name": "no geometry"}},
    ]}  # fmt: skip
    statuses = [(r["kind"], r["status"]) for r in emodnet_features("cicacables", collection)]
    assert statuses == [("telecom_cable", "operational"), ("telecom_cable", "abandoned")]


def test_international_cables_are_cut_to_spanish_waters():
    across = LineString([(-30, 36), (10, 36)])  # Atlantic to the Mediterranean
    elsewhere = LineString([(100, 10), (110, 10)])
    cables = {"features": [
        {"geometry": across.__geo_interface__, "properties": {"name": "ACROSS"}},
        {"geometry": elsewhere.__geo_interface__, "properties": {"name": "ELSEWHERE"}},
    ]}  # fmt: skip
    landings = {"features": [
        {"geometry": {"type": "Point", "coordinates": [2.17, 41.38]}, "properties": {"name": "Barcelona, Spain"}},
        {"geometry": {"type": "Point", "coordinates": [5.37, 43.3]}, "properties": {"name": "Marseille, France"}},
    ]}  # fmt: skip
    rows = list(telegeography_features(cables, landings))
    assert [(r["kind"], r["name"]) for r in rows] == [
        ("telecom_cable", "ACROSS"),
        ("landing_point", "Barcelona, Spain"),
    ]
    assert "-19 36" in rows[0]["geom"] and "-30 36" not in rows[0]["geom"]
