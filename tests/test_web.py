"""The dashboard's API: architecture rules, then every endpoint against a real, migrated PostGIS database.

The API tests need UATU_TEST_DATABASE_URL (see test_db.py); they only touch their own rows.
"""

import ast
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, insert

from uatu import db
from uatu.db.models import (
    AisEvent,
    DashboardUser,
    IngestSession,
    Position,
    RecordedZone,
    SarAisCheck,
    SarDetection,
    SarScene,
    ShipCompany,
    ShipLink,
    StraitTransit,
    Vessel,
    VesselRisk,
    VesselStop,
    WatchedVessel,
    WatchlistedVessel,
)
from uatu.web.config import WebSettings
from uatu.web.main import create_app

WEB = Path(__file__).parent.parent / "src" / "uatu" / "web"

# --- architecture --------------------------------------------------------------------------------


def test_api_is_read_only_but_for_what_you_sign_in_to_change():
    """No authentication to read: GET only, but for the watch list and the verdicts, which need a session,
    and signing in and out. All of them only from the dashboard's own page."""
    from fastapi.routing import APIRoute

    from uatu.web.auth.router import router as auth
    from uatu.web.dependencies import get_current_user, same_origin
    from uatu.web.reviews.router import router as reviews
    from uatu.web.watches.router import router as watches

    writable = {
        "/api/v1/watches", "/api/v1/watches/{watch_id}", "/api/v1/detections/{detection_id}/review",
        "/api/v1/auth/login", "/api/v1/auth/logout",
    }  # fmt: skip
    paths = create_app(WebSettings()).openapi()["paths"]
    assert any(path.startswith("/api/v1/") for path in paths)
    for path, operations in paths.items():
        assert set(operations) == {"get"} or path in writable, f"{path} accepts {set(operations)}"
    for router in (watches, reviews, auth):
        assert same_origin in [d.dependency for d in router.dependencies]
        for route in router.routes:
            assert isinstance(route, APIRoute)
            if route.methods - {"GET", "HEAD"} and router is not auth:
                assert get_current_user in [d.call for d in route.dependant.dependencies], route.path


@pytest.mark.parametrize("path", sorted(WEB.glob("*/service.py")), ids=lambda p: p.parent.name)
def test_services_know_nothing_of_http(path: Path):
    """Services are called from routers, scripts and tests alike."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            for name in names:
                assert not name.startswith(("fastapi", "starlette")), f"{path.parent.name}/service.py imports {name}"


def test_the_whole_coast_is_everything_recorded_even_with_few_zones_analysed():
    from uatu.config import ZONES
    from uatu.web.scope import scope

    strait = [ZONES["estrecho"]]
    whole = scope("todas", strait)
    assert whole is not None and whole.keys == ["estrecho"]  # passes and events: the analysed zones
    assert whole.bbox == (35.2, -6.5, 42.5, 4.4)  # vessels: wherever the recorder listens
    assert scope("cataluna", strait) is None  # recorded, but not analysed yet


# --- API ----------------------------------------------------------------------------------------

needs_db = pytest.mark.skipif(
    "UATU_TEST_DATABASE_URL" not in os.environ, reason="needs UATU_TEST_DATABASE_URL (a migrated PostGIS database)"
)

MMSI_A, MMSI_B, MMSI_C, MMSI_SILENT = 999_000_101, 999_000_102, 999_000_103, 999_000_104
MMSI_BARCELONA = 999_000_105  # in another zone: never in the Strait's dashboard
NOW = datetime.now(UTC)
SCENE_ID, SCENE2_ID = uuid.uuid4(), uuid.uuid4()  # two frames of one pass
SCENE_NAME = f"S1C_IW_GRDH_1SDV_TEST_{SCENE_ID.hex[:8]}_COG.SAFE"
SCENE2_NAME = f"S1C_IW_GRDH_1SDV_TEST_{SCENE2_ID.hex[:8]}_COG.SAFE"
DATATAKE = SCENE_ID.hex[:6].upper()
PASS_ID = f"S1C-{DATATAKE}-estrecho"
EVENT_KEY = f"test:{SCENE_ID.hex}"
SESSION_ID = uuid.uuid4()
COMPANY_ID = f"test-{SESSION_ID.hex[:8]}"
IMO_A, IMO_FLEETMATE = 9_999_901, 9_999_902
CELL = (35.931, -5.611)  # inside one 0.02° coverage cell


@pytest.fixture
async def rows():
    engine = db.create_engine(os.environ["UATU_TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(
            insert(IngestSession).values(
                id=SESSION_ID, connected_at=NOW - timedelta(hours=3), last_message_at=NOW, close_reason="test:web"
            )
        )
        await conn.execute(delete(RecordedZone).where(RecordedZone.key == "estrecho"))
        await conn.execute(insert(RecordedZone).values(key="estrecho", since=NOW - timedelta(hours=3)))
        await conn.execute(
            insert(Vessel).values(ais_class="A", first_seen_at=NOW, last_static_at=NOW),
            [
                {"mmsi": MMSI_A, "name": "TEST ALFA", "imo": IMO_A, "ship_type": 80, "dim_a": 200, "dim_b": 50},
                *(
                    {"mmsi": mmsi, "name": name, "imo": None, "ship_type": None, "dim_a": None, "dim_b": None}
                    for mmsi, name in [(MMSI_B, "TEST BRAVO"), (MMSI_C, "TEST CHARLIE"), (MMSI_SILENT, "TEST SILENT")]
                ),
            ],
        )
        company = {
            "id": COMPANY_ID,
            "name": "TEST SHIPMANAGEMENT",
            "kind": "company",
            "countries": "ae",
            "imos": "6000001",
            "sanctioned": True,
            "programs": "EU-RUS",
            "vessels": 2,
            "sanctioned_vessels": 0,
        }
        await conn.execute(insert(ShipCompany).values(company))
        await conn.execute(
            insert(ShipLink),
            [
                {
                    "vessel_imo": IMO_A,
                    "vessel_name": "ALFA AS LISTED",
                    "company_id": COMPANY_ID,
                    "role": "ism_manager",
                    "role_label": "security manager",
                    "source": "ua_war_sanctions",
                },
                {
                    "vessel_imo": IMO_A,
                    "vessel_name": "ALFA AS LISTED",
                    "company_id": COMPANY_ID,
                    "role": "commercial_manager",
                    "role_label": "commerce manager",
                    "source": "ua_war_sanctions",
                },
                {
                    "vessel_imo": IMO_FLEETMATE,
                    "vessel_name": "FLEETMATE",
                    "company_id": COMPANY_ID,
                    "role": "owner",
                    "role_label": "owner",
                    "source": "ua_war_sanctions",
                },
            ],
        )
        reason = {
            "kind": "sanctioned_company",
            "company_id": COMPANY_ID,
            "company": "TEST SHIPMANAGEMENT",
            "role": "ism_manager",
            "programs": "EU-RUS",
        }
        await conn.execute(
            insert(VesselRisk).values(imo=IMO_A, level="sanctioned_owner", reasons=[reason, {"kind": "shadow_fleet"}])
        )
        await conn.execute(
            insert(WatchlistedVessel).values(
                imo=IMO_A, watchlist="shadow_fleet", name="ALFA AS LISTED", note="Exports Urals.", source_id="test"
            )
        )  # fmt: skip
        await conn.execute(insert(WatchedVessel).values(name="test alfa", note="uatu test"))
        await conn.execute(
            insert(VesselStop).values(
                mmsi=MMSI_A, kind="anchorage", start_at=NOW - timedelta(hours=9), end_at=NOW - timedelta(hours=2),
                lat=35.93, lon=-5.30, coast_km=3.0, zone="estrecho",
            )
        )  # fmt: skip
        await conn.execute(
            insert(StraitTransit).values(mmsi=MMSI_A, at=NOW - timedelta(hours=10), direction="east", lat=35.95)
        )
        positions = []
        for i in range(4):  # four reports each, all in one cell: good reception there
            for mmsi in (MMSI_A, MMSI_B, MMSI_C):
                at = NOW - timedelta(minutes=40 - i * 5)
                positions.append(
                    {
                        "mmsi": mmsi,
                        "received_at": at,
                        "msg_type": 1,
                        "lat": CELL[0] + i * 0.001,
                        "lon": CELL[1],
                        "sog": 12.3456,
                        "cog": 270.04,
                    }
                )
        positions.append(positions[0] | {"mmsi": MMSI_BARCELONA, "lat": 41.35, "lon": 2.2})
        await conn.execute(insert(Position), positions)
        frame = {"zone": "estrecho", "platform": "S1C", "datatake": DATATAKE, "orbit_direction": "DESCENDING",
                 "relative_orbit": 1}  # fmt: skip
        await conn.execute(
            insert(SarScene),
            [
                frame
                | {
                    "id": SCENE_ID,
                    "product_id": SCENE_ID,
                    "name": SCENE_NAME,
                    "acquired_from": NOW - timedelta(hours=1),
                    "acquired_to": NOW - timedelta(minutes=59),
                    "footprint": "SRID=4326;POLYGON((-6 35.5,-5 35.5,-5 36.5,-6 36.5,-6 35.5))",
                    "aoi_coverage": 0.5,
                    "status": "processed",
                },
                frame
                | {  # overlaps the first by 0.2°: together 45% of the Strait's sea
                    "id": SCENE2_ID,
                    "product_id": SCENE2_ID,
                    "name": SCENE2_NAME,
                    "acquired_from": NOW - timedelta(minutes=59, seconds=35),
                    "acquired_to": NOW - timedelta(minutes=59, seconds=10),
                    "footprint": "SRID=4326;POLYGON((-5.2 35.5,-4.8 35.5,-4.8 36.5,-5.2 36.5,-5.2 35.5))",
                    "aoi_coverage": 0.2,
                    "status": "processed",
                },
            ],
        )
        detection = {
            "scene_id": SCENE_ID,
            "seen_at": NOW - timedelta(hours=1),
            "mmsi": None,
            "distance_m": None,
            "suspected_mmsi": None,
            "suspect_silent_minutes": None,
            "ais_vessels_nearby": None,
            "ais_reports_nearby": None,
            "pixels": 20,
            "peak_db": 5.0,
            "vv_db": 0.0,
            "vh_db": -9.87654,
            "background_db": -20.0,
        }
        await conn.execute(
            insert(SarDetection),
            [
                detection
                | {
                    "lat": 36.0,
                    "lon": -5.5,
                    "status": "matched",
                    "mmsi": MMSI_A,
                    "distance_m": 87.6,
                    "length_m": 240.4,
                },
                detection
                | {
                    "scene_id": SCENE2_ID,
                    "lat": 36.2,
                    "lon": -4.9,
                    "status": "no_coverage",
                    "length_m": 50.0,
                    "ais_vessels_nearby": 2,
                    "ais_reports_nearby": 3,
                    "suspected_mmsi": None,
                    "suspect_silent_minutes": None,
                },
                detection
                | {
                    "lat": 36.1,
                    "lon": -5.4,
                    "status": "dark",
                    "length_m": 120.0,
                    "suspected_mmsi": MMSI_SILENT,
                    "suspect_silent_minutes": 95.0,
                },
            ],
        )
        check = {"mmsi": MMSI_A, "seen_at": NOW - timedelta(hours=1), "lat": 36.0, "lon": -5.5, "length_m": 250,
                 "sog": 12.0, "report_gap_s": 10.0, "doppler_shift_m": -412.3}  # fmt: skip
        await conn.execute(
            insert(SarAisCheck),
            [
                check | {"scene_id": SCENE_ID, "status": "detected"},
                check | {"scene_id": SCENE2_ID, "status": "undetected"},
            ],
        )
        await conn.execute(
            insert(AisEvent).values(
                key=EVENT_KEY,
                kind="sanctioned_vessel",
                severity=3,
                mmsi=MMSI_A,
                start_at=NOW - timedelta(minutes=30),
                lat=CELL[0],
                lon=CELL[1],
                details={"imo": 9_999_901, "name": "TEST ALFA", "listed_as": "IMO 9999901", "programs": "TEST-1"},
            )
        )
        await conn.execute(
            insert(AisEvent).values(
                key=f"{EVENT_KEY}:barcelona",
                kind="loitering",
                severity=3,
                mmsi=MMSI_BARCELONA,
                start_at=NOW - timedelta(minutes=30),
                lat=41.35,
                lon=2.2,
                details={"hours": 3.0, "coast_km": 5.0},
            )
        )
    yield
    async with engine.begin() as conn:
        mmsis = [MMSI_A, MMSI_B, MMSI_C, MMSI_SILENT, MMSI_BARCELONA]
        await conn.execute(delete(SarScene).where(SarScene.id.in_([SCENE_ID, SCENE2_ID])))  # and their detections
        await conn.execute(delete(AisEvent).where(AisEvent.key.like(f"{EVENT_KEY}%")))
        await conn.execute(delete(Position).where(Position.mmsi.in_(mmsis)))
        await conn.execute(delete(Vessel).where(Vessel.mmsi.in_(mmsis)))
        await conn.execute(delete(VesselStop).where(VesselStop.mmsi.in_(mmsis)))
        await conn.execute(delete(StraitTransit).where(StraitTransit.mmsi.in_(mmsis)))
        await conn.execute(delete(ShipCompany).where(ShipCompany.id == COMPANY_ID))  # and its links
        await conn.execute(delete(VesselRisk).where(VesselRisk.imo == IMO_A))
        await conn.execute(delete(WatchlistedVessel).where(WatchlistedVessel.imo == IMO_A))
        await conn.execute(delete(WatchedVessel).where(WatchedVessel.note == "uatu test"))
        await conn.execute(delete(IngestSession).where(IngestSession.id == SESSION_ID))
        await conn.execute(delete(RecordedZone).where(RecordedZone.key == "estrecho"))
    await engine.dispose()


@pytest.fixture
async def client(rows, tmp_path):
    settings = WebSettings(
        database_url=os.environ["UATU_TEST_DATABASE_URL"], sar_data_dir=tmp_path, reports_dir=tmp_path / "reports"
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def ok(client: httpx.AsyncClient, path: str):
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()["data"]


@needs_db
async def test_passes(client, tmp_path):
    passes = await ok(client, "/api/v1/passes")
    ours = next(p for p in passes if p["id"] == PASS_ID)
    expected = {
        "frames": 2, "detections": 3, "matched": 1, "no_coverage": 1, "fixed": 0, "candidates": 1, "coverage": 0.45,
    }  # fmt: skip
    assert {k: ours[k] for k in expected} == expected
    assert ours["has_image"] is False
    assert ours["footprint"]["type"] == "Polygon"  # the two frames, merged

    detail = await ok(client, f"/api/v1/passes/{PASS_ID}")
    kinds = {f["properties"]["kind"]: f["properties"] for f in detail["detections_geojson"]["features"]}
    assert set(kinds) == {"matched", "went_silent", "no_coverage"}
    assert kinds["matched"]["vessel_name"] == "TEST ALFA"
    assert kinds["matched"]["length_m"] == 240
    # Where its AIS put it, from the frame that saw it: the echo shows shifted along the track.
    assert (kinds["matched"]["ais_lon"], kinds["matched"]["ais_lat"], kinds["matched"]["doppler_shift_m"]) == (
        -5.5, 36.0, -412
    )  # fmt: skip
    assert kinds["no_coverage"]["ais_lon"] is None
    assert kinds["matched"]["vh_db"] == -9.9
    assert kinds["went_silent"]["suspect_name"] == "TEST SILENT"
    assert (kinds["no_coverage"]["ais_vessels_nearby"], kinds["no_coverage"]["ais_reports_nearby"]) == (2, 3)
    assert detail["reception_rule"] == {
        "radius_m": 3000.0, "window_hours": 2.0, "min_vessels": 3, "min_reports_per_vessel": 3.0
    }  # fmt: skip
    checks = [f["properties"] for f in detail["ais_geojson"]["features"]]
    assert [(c["name"], c["status"]) for c in checks] == [("TEST ALFA", "detected")]  # seen in one frame: seen
    assert detail["images"] == []

    assert (await client.get(f"/api/v1/passes/{PASS_ID}/images/1")).status_code == 404
    overlay = tmp_path / "estrecho" / SCENE2_NAME.removesuffix(".SAFE")  # only the second frame has one
    overlay.mkdir(parents=True)
    (overlay / "backscatter.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (overlay / "backscatter.json").write_text("[[-6, 36.5], [-5, 36.5], [-5, 35.5], [-6, 35.5]]")
    detail = await ok(client, f"/api/v1/passes/{PASS_ID}")
    [image] = detail["images"]
    assert image["url"].startswith(f"/api/v1/passes/{PASS_ID}/images/1?v=")
    assert image["coordinates"] == [[-6, 36.5], [-5, 36.5], [-5, 35.5], [-6, 35.5]]
    response = await client.get(image["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert (await client.get(f"/api/v1/passes/{PASS_ID}/images/0")).status_code == 404
    assert (await client.get(f"/api/v1/passes/{PASS_ID}/images/5")).status_code == 404


@needs_db
async def test_events(client):
    events = await ok(client, "/api/v1/events?hours=2&min_severity=3&zone=estrecho")
    ours = next(f for f in events["features"] if f["properties"]["mmsi"] == MMSI_A)
    assert ours["properties"]["title"] == "Barco sancionado en la zona: TEST ALFA"
    assert ours["geometry"] == {"type": "Point", "coordinates": [CELL[1], CELL[0]]}
    assert all(f["properties"]["mmsi"] != MMSI_BARCELONA for f in events["features"])
    mmsis = {f["properties"]["mmsi"] for f in (await ok(client, "/api/v1/events?hours=2"))["features"]}
    assert {MMSI_A, MMSI_BARCELONA} <= mmsis  # the whole coast


SAME = {"Origin": "http://test"}  # the dashboard's own page
USER, PASSWORD = "uatu-test", "a test password"


@pytest.fixture
async def signed_in(client):
    """The client with a session of a test user."""
    from uatu.web import dependencies
    from uatu.web.__main__ import set_password

    dependencies._attempts.clear()
    await set_password(WebSettings(database_url=os.environ["UATU_TEST_DATABASE_URL"]), USER, PASSWORD)
    response = await client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD}, headers=SAME)
    assert response.status_code == 200, response.text
    client.cookies.set(dependencies.SESSION_COOKIE, _session_token(response))
    yield client
    client.cookies.clear()
    engine = db.create_engine(os.environ["UATU_TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(delete(DashboardUser).where(DashboardUser.username == USER))  # and its sessions
    await engine.dispose()


def _session_token(response: httpx.Response) -> str:
    cookie = response.headers["set-cookie"]
    attributes = {a.strip().split("=")[0].lower() for a in cookie.split(";")[1:]}
    assert {"httponly", "path", "samesite", "expires"} <= attributes and "samesite=strict" in cookie.lower()
    return cookie.split(";")[0].split("=", 1)[1]


@needs_db
async def test_signing_in_and_out(signed_in):
    from uatu.web import dependencies

    client = signed_in
    assert (await ok(client, "/api/v1/auth/session")) == {"username": USER}
    wrong = await client.post("/api/v1/auth/login", json={"username": USER, "password": "nope"}, headers=SAME)
    nobody = await client.post("/api/v1/auth/login", json={"username": "nobody", "password": "nope"}, headers=SAME)
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json()["error"]["message"] == nobody.json()["error"]["message"]  # says nothing of who exists
    elsewhere = await client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert elsewhere.status_code == 403  # no Origin: not the dashboard

    assert (await client.post("/api/v1/auth/logout", headers=SAME)).status_code == 204
    assert (await ok(client, "/api/v1/auth/session")) == {"username": None}
    refused = await client.post("/api/v1/watches", json={"mmsi": 1, "note": "uatu test"}, headers=SAME)
    assert refused.status_code == 401  # the old cookie no longer opens anything

    for _ in range(dependencies.LOGIN_ATTEMPTS):
        await client.post("/api/v1/auth/login", json={"username": USER, "password": "nope"}, headers=SAME)
    throttled = await client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD}, headers=SAME)
    assert throttled.status_code == 429
    dependencies._attempts.clear()


@needs_db
async def test_your_watch_list(signed_in):
    client = signed_in
    [fixture] = [w for w in await ok(client, "/api/v1/watches") if w["note"] == "uatu test"]
    assert (fixture["name"], [v["mmsi"] for v in fixture["vessels"]]) == ("test alfa", [MMSI_A])  # by name, any case

    added = await client.post("/api/v1/watches", json={"imo": IMO_A, "note": "uatu test"}, headers=SAME)
    assert added.status_code == 201, added.text
    watch = added.json()["data"]
    assert (watch["imo"], watch["mmsi"], [v["mmsi"] for v in watch["vessels"]]) == (IMO_A, None, [MMSI_A])
    detail = await ok(client, f"/api/v1/vessels/{MMSI_A}")
    assert sorted(w["id"] for w in detail["watched"]) == sorted([fixture["id"], watch["id"]])

    # Another site open in the same browser, or a request with no Origin at all, can't write.
    for headers in ({"Origin": "http://evil.example"}, {}):
        refused = await client.post("/api/v1/watches", json={"mmsi": 1, "note": "uatu test"}, headers=headers)
        assert refused.status_code == 403
        assert (await client.delete(f"/api/v1/watches/{watch['id']}", headers=headers)).status_code == 403
    nothing = await client.post("/api/v1/watches", json={"note": "uatu test"}, headers=SAME)
    assert nothing.status_code == 422
    too_short = await client.post("/api/v1/watches", json={"imo": 123, "note": "uatu test"}, headers=SAME)
    assert too_short.status_code == 422

    assert (await client.delete(f"/api/v1/watches/{watch['id']}", headers=SAME)).status_code == 204
    assert (await client.delete(f"/api/v1/watches/{watch['id']}", headers=SAME)).status_code == 404
    assert [w["id"] for w in await ok(client, "/api/v1/watches") if w["note"] == "uatu test"] == [fixture["id"]]


@needs_db
async def test_verdicts_on_detections(signed_in):
    client = signed_in
    [summary] = [p for p in await ok(client, "/api/v1/passes") if p["id"] == PASS_ID]
    assert summary["pending"] == 1  # the vessel gone silent
    kinds = {f["properties"]["kind"]: f["properties"] for f in (await ok(client, f"/api/v1/passes/{PASS_ID}"))[
        "detections_geojson"]["features"]}  # fmt: skip
    silent = kinds["went_silent"]
    assert silent["review"] is None

    path = f"/api/v1/detections/{silent['id']}/review"
    verdict = {"verdict": "vessel_without_ais", "mmsi": MMSI_SILENT, "note": "  its AIS off, by the radar  "}
    put = await client.put(path, json=verdict, headers=SAME)
    assert put.status_code == 200, put.text
    review = put.json()["data"]
    assert (review["verdict"], review["mmsi"], review["vessel_name"], review["note"], review["reviewed_by"]) == (
        "vessel_without_ais", MMSI_SILENT, "TEST SILENT", "its AIS off, by the radar", USER
    )  # fmt: skip
    changed = await client.put(path, json={"verdict": "reception_gap"}, headers=SAME)
    assert (changed.json()["data"]["verdict"], changed.json()["data"]["mmsi"]) == ("reception_gap", None)
    detail = await ok(client, f"/api/v1/passes/{PASS_ID}")
    reviewed = next(
        f["properties"] for f in detail["detections_geojson"]["features"] if f["properties"]["id"] == silent["id"]
    )
    assert reviewed["review"]["verdict"] == "reception_gap"
    assert detail["pending"] == 0

    no_ship = await client.put(path, json={"verdict": "not_a_vessel", "mmsi": MMSI_A}, headers=SAME)
    assert no_ship.status_code == 422  # not a vessel: no MMSI to give it
    assert (await client.put("/api/v1/detections/999999999/review", json=verdict, headers=SAME)).status_code == 404
    assert (await client.delete(path, headers=SAME)).status_code == 204
    assert (await client.delete(path, headers=SAME)).status_code == 404
    assert (await client.put(path, json=verdict, headers=SAME)).status_code == 200
    client.cookies.clear()
    assert (await client.put(path, json=verdict, headers=SAME)).status_code == 401
    assert (await client.delete(path, headers=SAME)).status_code == 401


@needs_db
async def test_vessels(client):
    live = await ok(client, "/api/v1/vessels/live?minutes=60&zone=estrecho")
    alfa = next(f["properties"] for f in live["features"] if f["properties"]["mmsi"] == MMSI_A)
    assert alfa["name"] == "TEST ALFA"
    assert (alfa["sanctioned"], alfa["risk"], alfa["watched"]) == (False, "sanctioned_owner", True)
    assert not any(f["properties"]["watched"] for f in live["features"] if f["properties"]["mmsi"] != MMSI_A)
    assert alfa["sog"] == 12.3
    assert alfa["cog"] == 270
    assert all(f["properties"]["mmsi"] != MMSI_BARCELONA for f in live["features"])
    catalonia = await ok(client, "/api/v1/vessels/live?minutes=60&zone=cataluna")
    assert [f["properties"]["mmsi"] for f in catalonia["features"] if f["properties"]["mmsi"] > 999_000_000] == [
        MMSI_BARCELONA
    ]

    found = await ok(client, "/api/v1/vessels?q=test%20bra")
    assert [v["mmsi"] for v in found] == [MMSI_B]

    detail = await ok(client, f"/api/v1/vessels/{MMSI_A}")
    assert detail["name"] == "TEST ALFA"
    assert detail["track"]["type"] == "LineString"
    assert [e["kind"] for e in detail["events"]] == ["sanctioned_vessel"]
    assert detail["risk"] == {
        "level": "sanctioned_owner",
        "reasons": [
            "su gestor ISM, TEST SHIPMANAGEMENT, está en listas de sanciones (EU-RUS)",
            "la inteligencia ucraniana (GUR) lo cuenta en la flota en la sombra rusa",
        ],
    }
    assert detail["watchlists"] == [{"watchlist": "shadow_fleet", "note": "Exports Urals."}]
    assert [w["note"] for w in detail["watched"]] == ["uatu test"]
    assert detail["last_position"]["properties"]["watched"] is True
    assert (await ok(client, f"/api/v1/vessels/{MMSI_SILENT}"))["watched"] == []
    [stop] = detail["stops"]
    assert (stop["kind"], stop["place"], stop["lat"]) == ("anchorage", None, 35.93)
    assert [t["direction"] for t in detail["transits"]] == ["east"]
    [manager] = detail["companies"]
    assert (manager["id"], manager["sanctioned"], manager["roles"]) == (
        COMPANY_ID,
        True,
        ["commercial_manager", "ism_manager"],
    )
    assert detail["radar_sightings"][0]["pass_id"] == PASS_ID

    silent = await ok(client, f"/api/v1/vessels/{MMSI_SILENT}")
    assert silent["radar_sightings"][0]["as_suspect"] is True

    missing = await client.get("/api/v1/vessels/999000199")
    assert missing.status_code == 404


@needs_db
async def test_companies(client):
    company = await ok(client, f"/api/v1/companies/{COMPANY_ID}")
    assert (company["name"], company["sanctioned"], company["countries"]) == ("TEST SHIPMANAGEMENT", True, ["ae"])
    # The vessel Uatu has heard comes first, with the MMSI it was heard with.
    assert [(v["imo"], v["mmsi"], v["ais_name"], v["risk"]) for v in company["fleet"]] == [
        (IMO_A, MMSI_A, "TEST ALFA", "sanctioned_owner"),
        (IMO_FLEETMATE, None, None, None),
    ]
    assert company["fleet"][0]["watchlists"] == ["shadow_fleet"]
    assert (await client.get("/api/v1/companies/no-such-company")).status_code == 404
    assert (await client.get("/api/v1/companies/bad%20id")).status_code == 422


@needs_db
async def test_history(client):
    span = await ok(client, "/api/v1/history")
    assert span["start"] <= span["end"] and isinstance(span["outages"], list)

    start = (NOW - timedelta(hours=1)).isoformat()
    response = await client.get("/api/v1/history/tracks", params={"start": start, "zone": "estrecho"})
    assert response.status_code == 200 and response.headers["cache-control"] == "public, max-age=60"
    tracks = {t["mmsi"]: t for t in response.json()["data"]["tracks"]}
    alfa = tracks[MMSI_A]
    assert (alfa["name"], alfa["risk"], len(alfa["t"])) == ("TEST ALFA", "sanctioned_owner", 4)
    assert alfa["t"] == sorted(alfa["t"]) and alfa["sog"][0] == 12.3 and alfa["cog"][0] == 270
    assert MMSI_BARCELONA not in tracks  # in another zone
    old = await client.get("/api/v1/history/tracks", params={"start": "2026-01-01T00:00Z", "hours": 1})
    assert old.json()["data"]["tracks"] == [] and old.headers["cache-control"] == "public, max-age=86400"
    assert (await client.get("/api/v1/history/tracks", params={"start": start, "hours": 4})).status_code == 422

    cells = await ok(client, "/api/v1/history/density?days=1&zone=estrecho")
    ours = [c for c in cells["features"] if abs(c["geometry"]["coordinates"][1] - 35.93) < 0.02]
    assert ours and all(0 < c["properties"]["weight"] <= 1 for c in cells["features"])


@needs_db
async def test_coverage_and_status(client):
    cells = await ok(client, "/api/v1/coverage?days=1")
    ours = [
        f["properties"]
        for f in cells["features"]
        if f["geometry"]["coordinates"][0][0][0] <= CELL[1] < f["geometry"]["coordinates"][0][1][0]
        and f["geometry"]["coordinates"][0][0][1] <= CELL[0] < f["geometry"]["coordinates"][0][2][1]
    ]
    assert len(ours) == 1
    assert ours[0]["vessels"] >= 3
    assert ours[0]["reception"] == "good"

    status = await ok(client, "/api/v1/status")
    assert status["vessels_last_hour"] >= 3
    assert status["alerts_24h"] >= 1
    assert status["latest_pass"] is not None


@needs_db
async def test_errors_share_one_shape(client):
    for path, status, code in [
        ("/api/v1/passes/S1Z-FFFFFF-estrecho", 404, "not_found"),
        ("/api/v1/passes/S1C-013AD6", 422, "invalid_input"),
        ("/api/v1/events?zone=marte", 404, "not_found"),
        ("/api/v1/passes/not-a-pass", 422, "invalid_input"),
        ("/api/v1/events?hours=0", 422, "invalid_input"),
        ("/api/v1/nothing-here", 404, "not_found"),
    ]:
        response = await client.get(path, headers={"X-Request-ID": "test-123"})
        assert response.status_code == status, path
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == "test-123"
        assert response.headers["x-request-id"] == "test-123"

    response = await client.post("/api/v1/passes")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


@needs_db
async def test_page_and_health(client):
    page = await client.get("/")
    assert page.status_code == 200
    assert "<title>Uatu</title>" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert (await client.get("/app.js")).status_code == 200
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).json() == {"status": "ok"}
    zones = await ok(client, "/api/v1/zones")
    strait = zones["zones"][0]
    assert (strait["key"], strait["name"], strait["north"]) == ("estrecho", "Estrecho de Gibraltar", 36.7)
    assert strait["recording_since"] is not None
    assert [z["key"] for z in zones["zones"]] == ["estrecho", "alboran", "murcia", "valencia", "cataluna", "baleares"]
    assert zones["whole_coast"] == {"west": -6.5, "south": 35.2, "east": 4.4, "north": 42.5}
    land = await ok(client, "/api/v1/map/land")  # no land mask here: Natural Earth everywhere
    assert land["features"]
    assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in land["features"])


def test_the_satellite_layer_is_maptiler_with_a_key_and_sentinel_2_without():
    from uatu.web.basemap.service import satellite

    maptiler = satellite("secret-key")
    assert maptiler.tiles == ["https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key=secret-key"]
    assert (maptiler.tile_size, "MapTiler" in maptiler.attribution) == (512, True)
    eox = satellite(None)
    assert "s2cloudless" in eox.tiles[0] and eox.tile_size == 256


def test_land_outlines_that_touch_themselves_are_mended(monkeypatch):
    """Traced from a raster, simplified and rounded, an outline can touch itself: GEOS refuses to clip it."""
    from uatu.web.basemap import service
    from uatu.web.schemas import Feature, FeatureCollection
    from uatu.web.schemas import Geometry as GeoJSON

    # A bow tie: two triangles meeting at one point, crossing there. Invalid.
    bow_tie = GeoJSON(
        type="Polygon", coordinates=[[[-2.0, 37.0], [-1.9, 37.1], [-1.9, 37.0], [-2.0, 37.1], [-2.0, 37.0]]]
    )
    outlines = FeatureCollection(features=[Feature(geometry=bow_tie, properties=service.LandProperties())])
    monkeypatch.setattr(service, "land_outlines", lambda grid, path: outlines)
    monkeypatch.setattr(service, "context_land", lambda covered: FeatureCollection(features=[]))
    land = service.land([(None, None, (36.9, -2.1, 37.2, -1.8))])
    assert len(land.features) == 1 and land.features[0].geometry.type in ("Polygon", "MultiPolygon")


@needs_db
async def test_reports(client, tmp_path):
    folder = tmp_path / "reports" / "informe-2026-10-18"
    folder.mkdir(parents=True)
    (folder / "informe.html").write_text("<!doctype html><title>Uatu</title>")
    (folder / "densidad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "reports" / "not-a-report").mkdir()
    reports = await ok(client, "/api/v1/reports")
    assert reports == [{"name": "informe-2026-10-18", "last_day": "2026-10-18",
                        "url": "/api/v1/reports/informe-2026-10-18/informe.html"}]  # fmt: skip
    page = await client.get(reports[0]["url"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert (await client.get("/api/v1/reports/informe-2026-10-18/densidad.png")).headers["content-type"] == "image/png"
    for bad in (
        "informe-2026-10-18/..%2F..%2Fsecret.html",
        "../informe.html",
        "informe-2026-10-18/x.sh",
        "informe-x/informe.html",
    ):
        assert (await client.get(f"/api/v1/reports/{bad}")).status_code == 404


@needs_db
async def test_the_sea_a_pass_did_not_image(client):
    from shapely.geometry import shape

    whole = await ok(client, "/api/v1/map/unimaged?zone=estrecho")
    outside = await ok(client, f"/api/v1/map/unimaged?zone=estrecho&pass={PASS_ID}")
    [sea], [rest] = whole["features"], outside["features"]
    assert rest["properties"]["imaged_by"] == PASS_ID
    assert 0 < shape(rest["geometry"]).area < shape(sea["geometry"]).area  # the pass's footprint cut out
    assert (await client.get("/api/v1/map/unimaged?pass=../etc")).status_code == 422
