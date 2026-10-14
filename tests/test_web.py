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
from uatu.db.models import AisEvent, IngestSession, Position, SarAisCheck, SarDetection, SarScene, Vessel
from uatu.web.config import WebSettings
from uatu.web.main import create_app

WEB = Path(__file__).parent.parent / "src" / "uatu" / "web"

# --- architecture --------------------------------------------------------------------------------


def test_api_is_read_only():
    """No authentication, so nothing in the API may change anything: GET only."""
    paths = create_app(WebSettings()).openapi()["paths"]
    assert any(path.startswith("/api/v1/") for path in paths)
    for path, operations in paths.items():
        assert set(operations) == {"get"}, f"{path} accepts {set(operations)}"


@pytest.mark.parametrize("path", sorted(WEB.glob("*/service.py")), ids=lambda p: p.parent.name)
def test_services_know_nothing_of_http(path: Path):
    """Services are called from routers, scripts and tests alike."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            for name in names:
                assert not name.startswith(("fastapi", "starlette")), f"{path.parent.name}/service.py imports {name}"


# --- API ----------------------------------------------------------------------------------------

needs_db = pytest.mark.skipif(
    "UATU_TEST_DATABASE_URL" not in os.environ, reason="needs UATU_TEST_DATABASE_URL (a migrated PostGIS database)"
)

MMSI_A, MMSI_B, MMSI_C, MMSI_SILENT = 999_000_101, 999_000_102, 999_000_103, 999_000_104
NOW = datetime.now(UTC)
SCENE_ID, SCENE2_ID = uuid.uuid4(), uuid.uuid4()  # two frames of one pass
SCENE_NAME = f"S1C_IW_GRDH_1SDV_TEST_{SCENE_ID.hex[:8]}_COG.SAFE"
SCENE2_NAME = f"S1C_IW_GRDH_1SDV_TEST_{SCENE2_ID.hex[:8]}_COG.SAFE"
DATATAKE = SCENE_ID.hex[:6].upper()
PASS_ID = f"S1C-{DATATAKE}"
EVENT_KEY = f"test:{SCENE_ID.hex}"
SESSION_ID = uuid.uuid4()
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
        await conn.execute(
            insert(Vessel).values(ais_class="A", first_seen_at=NOW, last_static_at=NOW),
            [
                {"mmsi": MMSI_A, "name": "TEST ALFA", "imo": 9_999_901, "ship_type": 80, "dim_a": 200, "dim_b": 50},
                *(
                    {"mmsi": mmsi, "name": name, "imo": None, "ship_type": None, "dim_a": None, "dim_b": None}
                    for mmsi, name in [(MMSI_B, "TEST BRAVO"), (MMSI_C, "TEST CHARLIE"), (MMSI_SILENT, "TEST SILENT")]
                ),
            ],
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
        await conn.execute(insert(Position), positions)
        frame = {"platform": "S1C", "datatake": DATATAKE, "orbit_direction": "DESCENDING", "relative_orbit": 1}
        await conn.execute(
            insert(SarScene),
            [
                frame
                | {
                    "id": SCENE_ID,
                    "name": SCENE_NAME,
                    "acquired_from": NOW - timedelta(hours=1),
                    "acquired_to": NOW - timedelta(minutes=59),
                    "footprint": "SRID=4326;POLYGON((-6 35.5,-5 35.5,-5 36.5,-6 36.5,-6 35.5))",
                    "aoi_coverage": 0.5,
                    "status": "processed",
                },
                frame
                | {  # overlaps the first by 0.2°: together they cover 60% of the area, not 70%
                    "id": SCENE2_ID,
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
                 "sog": 12.0, "report_gap_s": 10.0, "doppler_shift_m": 0.0}  # fmt: skip
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
    yield
    async with engine.begin() as conn:
        mmsis = [MMSI_A, MMSI_B, MMSI_C, MMSI_SILENT]
        await conn.execute(delete(SarScene).where(SarScene.id.in_([SCENE_ID, SCENE2_ID])))  # and their detections
        await conn.execute(delete(AisEvent).where(AisEvent.key == EVENT_KEY))
        await conn.execute(delete(Position).where(Position.mmsi.in_(mmsis)))
        await conn.execute(delete(Vessel).where(Vessel.mmsi.in_(mmsis)))
        await conn.execute(delete(IngestSession).where(IngestSession.id == SESSION_ID))
    await engine.dispose()


@pytest.fixture
async def client(rows, tmp_path):
    settings = WebSettings(database_url=os.environ["UATU_TEST_DATABASE_URL"], sar_data_dir=tmp_path)
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
        "frames": 2, "detections": 3, "matched": 1, "no_coverage": 1, "fixed": 0, "candidates": 1, "coverage": 0.6,
    }  # fmt: skip
    assert {k: ours[k] for k in expected} == expected
    assert ours["has_image"] is False
    assert ours["footprint"]["type"] == "Polygon"  # the two frames, merged

    detail = await ok(client, f"/api/v1/passes/{PASS_ID}")
    kinds = {f["properties"]["kind"]: f["properties"] for f in detail["detections_geojson"]["features"]}
    assert set(kinds) == {"matched", "went_silent", "no_coverage"}
    assert kinds["matched"]["vessel_name"] == "TEST ALFA"
    assert kinds["matched"]["length_m"] == 240
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
    overlay = tmp_path / SCENE2_NAME.removesuffix(".SAFE")  # only the second frame has one
    overlay.mkdir()
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
    events = await ok(client, "/api/v1/events?hours=2&min_severity=3")
    ours = next(f for f in events["features"] if f["properties"]["mmsi"] == MMSI_A)
    assert ours["properties"]["title"] == "Barco sancionado en la zona: TEST ALFA"
    assert ours["geometry"] == {"type": "Point", "coordinates": [CELL[1], CELL[0]]}


@needs_db
async def test_vessels(client):
    live = await ok(client, "/api/v1/vessels/live?minutes=60")
    alfa = next(f["properties"] for f in live["features"] if f["properties"]["mmsi"] == MMSI_A)
    assert alfa["name"] == "TEST ALFA"
    assert alfa["sog"] == 12.3
    assert alfa["cog"] == 270

    found = await ok(client, "/api/v1/vessels?q=test%20bra")
    assert [v["mmsi"] for v in found] == [MMSI_B]

    detail = await ok(client, f"/api/v1/vessels/{MMSI_A}")
    assert detail["name"] == "TEST ALFA"
    assert detail["track"]["type"] == "LineString"
    assert [e["kind"] for e in detail["events"]] == ["sanctioned_vessel"]
    assert detail["radar_sightings"][0]["pass_id"] == PASS_ID

    silent = await ok(client, f"/api/v1/vessels/{MMSI_SILENT}")
    assert silent["radar_sightings"][0]["as_suspect"] is True

    missing = await client.get("/api/v1/vessels/999000199")
    assert missing.status_code == 404


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
        ("/api/v1/passes/S1Z-FFFFFF", 404, "not_found"),
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
    assert (await ok(client, "/api/v1/map/area")) == {"west": -6.5, "south": 35.5, "east": -4.5, "north": 36.5}
    land = await ok(client, "/api/v1/map/land")  # no land mask here: Natural Earth everywhere
    assert land["features"]
    assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in land["features"])
