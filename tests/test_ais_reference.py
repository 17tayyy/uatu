from shapely import LineString

from uatu.ais.reference import emodnet_features, telegeography_features
from uatu.ais.store import valid_imo


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


def test_world_ports_by_the_names_people_use():
    from uatu.ais.reference import world_ports

    def row(number, name, country, lat, lon):
        return {"World Port Index Number": number, "Main Port Name": name, "Country Code": country,
                "UN/LOCODE": "", "Harbor Size": "Medium", "Latitude": lat, "Longitude": lon}  # fmt: skip

    rows = [
        row("38330.0", "Europa Point", "Gibraltar", "36.13", "-5.35"),
        row("45730.0", "Ceuta", "Spain", "35.9", "-5.32"),
        row("", "Broken", "Nowhere", "x", "y"),
        row("45730.0", "Ceuta", "Spain", "35.89", "-5.32"),  # the same number again: the later row wins
    ]
    gibraltar, ceuta = world_ports(rows)
    assert (gibraltar["id"], gibraltar["name"], gibraltar["locode"]) == (38330, "Gibraltar", None)
    assert (ceuta["name"], ceuta["lat"], ceuta["lon"]) == ("Ceuta", 35.89, -5.32)
