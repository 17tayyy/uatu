"""Integration tests against a real, migrated PostGIS database.

Run with UATU_TEST_DATABASE_URL set to a database no recorder is writing to, e.g. the local
docker compose one. They only touch their own rows and delete them afterwards.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, update

from uatu import db
from uatu.db.models import IngestSession, Position, StaticReport, Vessel
from uatu.recorder.messages import PositionRow, StaticRow
from uatu.recorder.state import Session, Stats
from uatu.recorder.writer import DbWriter

pytestmark = pytest.mark.skipif(
    "UATU_TEST_DATABASE_URL" not in os.environ, reason="needs UATU_TEST_DATABASE_URL (a migrated PostGIS database)"
)

MMSI_A, MMSI_B = 999_000_001, 999_000_002
T0 = datetime(2026, 10, 13, 6, 30, tzinfo=UTC)


@pytest.fixture
async def engine():
    engine = db.create_engine(os.environ["UATU_TEST_DATABASE_URL"])
    yield engine
    async with engine.begin() as conn:
        for model in (Position, StaticReport, Vessel):
            await conn.execute(delete(model).where(model.mmsi.in_([MMSI_A, MMSI_B])))
        await conn.execute(delete(IngestSession).where(IngestSession.close_reason.like("test:%")))
    await engine.dispose()


async def write(engine, rows, sessions=()):
    stats = Stats()
    writer = DbWriter(engine, stats, batch_size=100, flush_interval_s=0.01, queue_max=1000)
    for session in sessions:
        writer.track(session)
    writer.start()
    for row in rows:
        writer.offer(row)
    await writer.close(timeout_s=5)
    return stats


async def test_positions_get_a_geography_point(engine):
    stats = await write(engine, [PositionRow(MMSI_A, T0, 1, 36.0, -5.6, 12.3, 81.5, 80, 0, True)])
    assert stats.positions_written == 1
    async with engine.connect() as conn:
        # Distance in metres from Tarifa's lighthouse, computed by PostGIS from the generated column.
        tarifa = func.ST_GeogFromText("POINT(-5.6097 36.0012)")
        distance = await conn.scalar(select(func.ST_Distance(Position.geom, tarifa)).where(Position.mmsi == MMSI_A))
    assert 800 < distance < 1000


async def test_vessels_merge_partial_reports_across_batches(engine):
    await write(engine, [StaticRow(MMSI_B, T0, 24, callsign="EA1234", ship_type=36, dim_a=10, dim_b=2)])
    await write(engine, [StaticRow(MMSI_B, T0 + timedelta(minutes=5), 24, name="LUNA")])
    async with engine.connect() as conn:
        vessel = (await conn.execute(select(Vessel).where(Vessel.mmsi == MMSI_B))).one()
        reports = await conn.scalar(select(func.count()).where(StaticReport.mmsi == MMSI_B))
    assert (vessel.name, vessel.callsign, vessel.ship_type, vessel.length_m) == ("LUNA", "EA1234", 36, 12)
    assert (vessel.first_seen_at, vessel.last_static_at) == (T0, T0 + timedelta(minutes=5))
    assert reports == 2


async def test_sessions_are_persisted_until_closed(engine):
    session = Session(connected_at=T0)
    session.on_message(T0 + timedelta(seconds=30))
    session.close("test: done")
    await write(engine, [], sessions=[session])
    async with engine.connect() as conn:
        row = (await conn.execute(select(IngestSession).where(IngestSession.id == session.id))).one()
    assert (row.messages, row.last_message_at, row.close_reason) == (1, T0 + timedelta(seconds=30), "test: done")


async def test_sar_scene_lifecycle(engine):
    import numpy as np
    from affine import Affine
    from rasterio.crs import CRS
    from shapely import box

    from uatu.db.models import SarAisCheck, SarDetection, SarScene
    from uatu.sar import store
    from uatu.sar.ais import AisVessel, Coverage
    from uatu.sar.analysis import Analysis
    from uatu.sar.annotation import AcquisitionGeometry
    from uatu.sar.catalog import Scene
    from uatu.sar.detect import Detection
    from uatu.sar.match import Expected, Match, MatchResult
    from uatu.sar.pipeline import SceneOutput
    from uatu.sar.raster import Grid

    product_id = "00000000-0000-0000-0000-00000000beef"
    name = "S1C_IW_GRDH_1SDV_20261013T182543_20261013T182608_009870_013AD6_TEST.SAFE"
    scene = Scene(product_id, name, T0, T0 + timedelta(seconds=25), "ASCENDING", 147, 0, True, box(-7, 35, -5, 37))
    scene_id = str(store.scene_id(product_id, "estrecho"))
    try:
        queued = await store.queue_new(engine, [scene], {product_id: 0.42}, "estrecho")
        assert queued == [store.ZoneScene(uuid.UUID(scene_id), "estrecho", scene)]
        assert await store.queue_new(engine, [scene], {product_id: 0.42}, "estrecho") == []  # already known
        other = await store.queue_new(engine, [scene], {product_id: 0.1}, "alboran")  # the same frame, another zone
        assert [s.zone for s in other] == ["alboran"]
        assert sorted(s.zone for s in await store.queued(engine)) == ["alboran", "estrecho"]
        loaded = await store.load(engine, uuid.UUID(scene_id))
        assert loaded.zone == "estrecho" and loaded.scene.id == product_id
        assert loaded.scene.name == scene.name and loaded.scene.footprint.equals(scene.footprint)
        assert await store.product_still_needed(engine, loaded)  # queued for alboran too: keep the download

        def det(lon: float, lat: float) -> Detection:
            return Detection(lon, lat, 0, 0, 20, 150, 20, 10, -8, -20)

        matched, dark, uncovered = det(-5.6, 36.0), det(-5.5, 36.0), det(-6.3, 36.1)
        vessel = AisVessel(MMSI_A, "TEST", 70, 150, -5.6, 36.0, 12.0, 90.0, T0, 30.0)
        expected = Expected(vessel, 0, 0, -120.0, (0, 0), 1000.0)
        nearby = {dark: Coverage(40, 400), uncovered: Coverage(2, 3)}
        result = MatchResult([Match(matched, expected, 48.0)], [dark], [], [], [uncovered], nearby)
        corners = np.array([(-7.0, 35.0), (-4.0, 35.0), (-7.0, 37.0), (-4.0, 37.0)])
        geometry = AcquisitionGeometry(T0, 347.0, 7600.0, corners, np.array([0, 0, 25, 25.0]), np.full(4, 850e3),
                                       np.full(4, 35.0))  # fmt: skip
        grid = Grid(CRS.from_epsg(32630), Affine(80, 0, 0, 0, -80, 0), 1, 1)
        output = SceneOutput([matched, dark, uncovered], grid, np.zeros((1, 1)), np.zeros((1, 1), bool),
                             np.zeros((1, 1), bool))  # fmt: skip
        analysis = Analysis(scene.name, geometry, output, result, ais_vessels=1)

        for _ in range(2):  # saving twice replaces, never duplicates
            summary = await store.save_results(engine, uuid.UUID(scene_id), analysis)
        assert summary == {
            "detections": 3,
            "matched": 1,
            "dark": 1,
            "no_coverage": 1,
            "ais_vessels": 1,
            "ais_undetected": 0,
        }

        async with engine.connect() as conn:
            row = (await conn.execute(select(SarScene).where(SarScene.id == uuid.UUID(scene_id)))).one()
            detections = (await conn.execute(select(SarDetection).where(SarDetection.scene_id == row.id))).all()
            checks = (await conn.execute(select(SarAisCheck).where(SarAisCheck.scene_id == row.id))).all()
        assert (row.status, row.matched, row.dark) == ("processed", 1, 1)
        assert sorted((d.status, d.mmsi, d.ais_vessels_nearby, d.ais_reports_nearby) for d in detections) == [
            ("dark", None, 40, 400), ("matched", MMSI_A, None, None), ("no_coverage", None, 2, 3)
        ]  # fmt: skip
        assert [(c.mmsi, c.status) for c in checks] == [(MMSI_A, "detected")]
        assert all(T0 <= d.seen_at <= T0 + timedelta(seconds=25) for d in detections)

        await store.mark_failed(engine, other[0].id, "boom")
        assert not await store.product_still_needed(engine, loaded)
        await store.mark_failed(engine, uuid.UUID(scene_id), "boom")
        assert await store.queued(engine) == []
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.product_id == uuid.UUID(product_id)))


async def test_ais_health_check_follows_the_recorder(engine):
    from uatu.db.models import IngestSession
    from uatu.ops import check_ais

    session = Session(connected_at=datetime.now(UTC) - timedelta(hours=1))
    session.on_message(datetime.now(UTC) - timedelta(minutes=40))
    session.close("test: stalled recorder")
    await write(engine, [], sessions=[session])
    try:
        stale = await check_ais(engine, max_age=timedelta(minutes=15))
        assert not stale.ok and "40 min" in stale.detail
        assert (await check_ais(engine, max_age=timedelta(hours=1))).ok
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(IngestSession).where(IngestSession.id == session.id))


async def test_ais_events_extend_rather_than_duplicate(engine):
    from uatu.ais import store
    from uatu.ais.events import Event
    from uatu.db.models import AisEvent

    first = Event("encounter", 2, MMSI_A, T0, 36.0, -5.6, {"hours": 2.0}, T0 + timedelta(hours=2), MMSI_B)
    # Next run: the window has slid, so the same encounter is seen starting later and lasting longer.
    later = Event(
        "encounter", 2, MMSI_A, T0 + timedelta(hours=1), 36.0, -5.6, {"hours": 2.0}, T0 + timedelta(hours=3), MMSI_B
    )
    try:
        assert await store.save(engine, [first]) == [first]
        assert await store.save(engine, [later]) == []  # extended, not new
        async with engine.connect() as conn:
            rows = (await conn.execute(select(AisEvent).where(AisEvent.mmsi == MMSI_A))).all()
        assert len(rows) == 1
        assert (rows[0].start_at, rows[0].end_at, rows[0].details["hours"]) == (T0, T0 + timedelta(hours=3), 3.0)
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(AisEvent).where(AisEvent.mmsi == MMSI_A))


async def test_sanctioned_vessels_seen_in_the_area_are_events(engine):
    from uatu.ais import store
    from uatu.db.models import SanctionedVessel

    async with engine.begin() as conn:
        await conn.execute(
            insert(SanctionedVessel).values(imo=9246322, name="TEX", programs="US-IRAN", source_id="test")
        )
    await write(engine, [StaticRow(MMSI_A, T0, 5, name="FLEXI TEST", imo=9246322)])
    await write(engine, [PositionRow(MMSI_A, T0 + timedelta(minutes=5), 1, 36.1, -5.5, 10.0, 90.0, 90, 0, True)])
    try:
        [event] = await store.sanctioned_present(engine, T0, T0 + timedelta(hours=1))
        assert (event.kind, event.severity, event.mmsi) == ("sanctioned_vessel", 3, MMSI_A)
        assert event.details["listed_as"] == "TEX" and event.details["matched_by"] == "imo"
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SanctionedVessel).where(SanctionedVessel.source_id == "test"))


async def test_vessels_with_sanctioned_managers_or_on_watchlists_are_events(engine):
    from uatu.ais import store
    from uatu.ais.analysis import describe
    from uatu.db.models import VesselRisk

    manager = {"kind": "sanctioned_company", "company_id": "NK-test", "company": "TEST SHIPMANAGEMENT",
               "role": "ism_manager", "programs": "EU-RUS"}  # fmt: skip
    async with engine.begin() as conn:
        await conn.execute(
            insert(VesselRisk),
            [
                {"imo": 9_999_801, "level": "sanctioned_owner", "reasons": [manager, {"kind": "shadow_fleet"}]},
                {"imo": 9_999_802, "level": "watchlisted", "reasons": [{"kind": "port_ban"}]},
            ],
        )
    await write(
        engine,
        [
            StaticRow(MMSI_A, T0, 5, name="DARK TEST", imo=9_999_801),
            StaticRow(MMSI_B, T0, 5, name="BAN TEST", imo=9_999_802),
        ],
    )
    await write(
        engine,
        [
            PositionRow(MMSI_A, T0 + timedelta(minutes=5), 1, 36.1, -5.5, 10.0, 90.0, 90, 0, True),
            PositionRow(MMSI_B, T0 + timedelta(minutes=6), 1, 36.2, -5.4, 10.0, 90.0, 90, 0, True),
        ],
    )
    try:
        events = {e.mmsi: e for e in await store.network_present(engine, T0, T0 + timedelta(hours=1))}
        assert (events[MMSI_A].kind, events[MMSI_A].severity) == ("sanctioned_owner", 3)
        assert (events[MMSI_B].kind, events[MMSI_B].severity) == ("watchlisted_vessel", 2)
        title, message, _ = describe(events[MMSI_A], {})
        assert title == "Barco con armador o gestor sancionado: DARK TEST"
        assert "no figura en listas de sanciones, pero su gestor ISM, TEST SHIPMANAGEMENT, está en listas" in message
        assert "flota en la sombra" in message
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(VesselRisk).where(VesselRisk.imo.in_([9_999_801, 9_999_802])))


async def test_a_stop_seen_in_pieces_is_one_and_a_watchlisted_vessel_stopping_is_an_event(engine):
    from uatu.ais import analysis, store
    from uatu.ais.stops import Port, Stop, Transit
    from uatu.db.models import Port as PortRow
    from uatu.db.models import StraitTransit, VesselRisk, VesselStop

    port = Port(999_001, "TEST PORT", "Spain", -5.32, 35.9)
    h = timedelta(hours=1)
    first = Stop(MMSI_A, "anchorage", T0, T0 + 3 * h, 35.93, -5.30, 3000.0, port, 3500.0)
    later = Stop(MMSI_A, "anchorage", T0 + 5 * h, T0 + 9 * h, 35.931, -5.301, 3000.0, port, 3500.0)
    elsewhere = Stop(MMSI_A, "berth", T0 + 9.5 * h, T0 + 10.5 * h, 36.13, -5.43, 200.0, None, None)  # 25 km on
    reason = [{"kind": "shadow_fleet"}]
    async with engine.begin() as conn:
        await conn.execute(insert(PortRow).values(id=port.id, name=port.name, country="Spain", lat=35.9, lon=-5.32))
        await conn.execute(insert(VesselRisk).values(imo=9_999_801, level="watchlisted", reasons=reason))
    await write(engine, [StaticRow(MMSI_A, T0, 5, name="ANCHOR TEST", imo=9_999_801)])
    try:
        await store.save_stops(engine, [first], "estrecho")
        [kept] = await store.save_stops(engine, [later], "estrecho")  # unheard for 2 h, in the same place
        assert (kept.start, kept.end) == (T0, T0 + 9 * h)
        await store.save_stops(engine, [elsewhere], "estrecho")
        async with engine.connect() as conn:
            rows = (await conn.execute(select(VesselStop).where(VesselStop.mmsi == MMSI_A).order_by("start_at"))).all()
        assert [(r.kind, r.start_at, r.end_at, r.port_id) for r in rows] == [
            ("anchorage", T0, T0 + 9 * h, port.id),
            ("berth", T0 + 9.5 * h, T0 + 10.5 * h, None),
        ]

        [event] = await analysis.listed_stops(engine, [kept])
        assert (event.kind, event.severity, event.details["hours"]) == ("listed_stop", 2, 9.0)
        title, message, _ = analysis.describe(event, {})
        assert title == "Barco vigilado fondeado frente a TEST PORT: ANCHOR TEST"
        assert message.startswith("ANCHOR TEST (MMSI 999000001) estuvo 9.0 h fondeado frente a TEST PORT.")
        assert [r.name for r in await store.stops_since(engine, T0 - h, listed_only=True)] == ["ANCHOR TEST"] * 2

        crossing = Transit(MMSI_A, T0, "east", 35.95)
        assert await store.save_transits(engine, [crossing]) == 1
        assert await store.save_transits(engine, [crossing]) == 0  # seen again: the same crossing
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(VesselStop).where(VesselStop.mmsi == MMSI_A))
            await conn.execute(delete(StraitTransit).where(StraitTransit.mmsi == MMSI_A))
            await conn.execute(delete(VesselRisk).where(VesselRisk.imo == 9_999_801))
            await conn.execute(delete(PortRow).where(PortRow.id == port.id))


async def test_a_sanctioned_vessels_visit_is_one_event_and_severity_follows_the_latest(engine):
    from uatu.ais import store
    from uatu.ais.events import Event
    from uatu.db.models import AisEvent

    def seen(start_h: float, end_h: float, severity: int = 3, kind: str = "sanctioned_vessel") -> Event:
        start, end = T0 + timedelta(hours=start_h), T0 + timedelta(hours=end_h)
        return Event(kind, severity, MMSI_A, start, 36.0, -5.6, {"name": "TEST"}, end)

    try:
        assert len(await store.save(engine, [seen(0, 2)])) == 1
        # Seen again after midnight, past a 3 h stretch with no reception: the same visit.
        assert await store.save(engine, [seen(5, 6)]) == []
        # A cable event re-detected with more history behind it drops to informative.
        assert len(await store.save(engine, [seen(0, 1, kind="infrastructure_loitering")])) == 1
        assert await store.save(engine, [seen(0, 1.5, severity=1, kind="infrastructure_loitering")]) == []
        async with engine.connect() as conn:
            rows = (await conn.execute(select(AisEvent).where(AisEvent.mmsi == MMSI_A).order_by(AisEvent.kind))).all()
        assert [(r.kind, r.severity, r.end_at - T0) for r in rows] == [
            ("infrastructure_loitering", 1, timedelta(hours=1.5)),
            ("sanctioned_vessel", 3, timedelta(hours=6)),
        ]
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(AisEvent).where(AisEvent.mmsi == MMSI_A))


async def test_fixed_objects_and_the_scene_report(engine):
    from shapely import box

    from uatu.db.models import SarDetection, SarScene
    from uatu.sar import store

    footprint = f"SRID=4326;{box(-6.0, 35.5, -5.0, 36.5).wkt}"
    scene_ids = [uuid.UUID(f"00000000-0000-0000-0000-0000000fe{k:03d}") for k in range(4)]

    def scene(k: int) -> dict:
        start = T0 - timedelta(days=6 * (3 - k))
        return {
            "id": scene_ids[k], "product_id": scene_ids[k], "zone": "estrecho", "name": f"S1C_TEST_{k}",
            "platform": "S1C", "datatake": f"TEST{k:02d}",
            "orbit_direction": "ASCENDING", "relative_orbit": 147, "acquired_from": start,
            "acquired_to": start + timedelta(seconds=25), "footprint": footprint, "aoi_coverage": 0.5,
            "status": "processed",
        }  # fmt: skip

    def detection(k: int, lon: float, lat: float, status: str = "unpaired", **suspect) -> dict:
        return {
            "scene_id": scene_ids[k], "seen_at": T0, "lat": lat, "lon": lon, "status": status, "pixels": 20,
            "length_m": 120.0, "peak_db": 15.0, "vv_db": 10.0,
            "suspected_mmsi": None, "suspect_silent_minutes": None, **suspect,  # same keys in every row
        }  # fmt: skip

    buoy = (-5.5, 36.0)  # in 3 of the 4 passes over it: fixed
    rows = [detection(k, *buoy, status="dark" if k == 3 else "unpaired") for k in range(3)]
    rows += [detection(2, -5.4, 36.05), detection(3, -5.4, 36.0501)]  # twice: recurring, not fixed
    rows += [
        detection(3, -5.30, 36.10, "dark"),  # nobody: unidentified
        detection(
            3, -5.25, 36.10, "dark", suspected_mmsi=MMSI_A, suspect_silent_minutes=95.0, suspect_distance_m=900.0
        ),
        detection(
            3, -5.20, 36.10, "dark", suspected_mmsi=MMSI_B, suspect_silent_minutes=12.0, suspect_distance_m=150.0
        ),
        detection(3, -5.15, 36.10, "matched"),
    ]
    rows[2]["status"] = "dark"  # the buoy, seen in the latest pass: not a candidate
    rows[2]["scene_id"] = scene_ids[3]
    try:
        async with engine.begin() as conn:
            await conn.execute(insert(SarScene), [scene(k) for k in range(4)])
            await conn.execute(insert(SarDetection), rows)
        assert await store.rebuild_fixed_objects(engine, srid=32630) == {"recurring": 2, "fixed": 1}

        report = await store.pass_report(engine, [scene_ids[3]])
        assert (report.detections, report.matched, report.at_fixed_objects) == (6, 1, 1)
        assert sorted(c.kind for c in report.candidates) == ["reception_gap", "unidentified", "went_silent"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.id.in_(scene_ids)))


async def test_a_frame_published_twice_is_queued_once(engine):
    from shapely import box

    from uatu.db.models import SarScene
    from uatu.sar import store
    from uatu.sar.catalog import Scene

    frame = "S1C_IW_GRDH_1SDV_20261014T062636_20261014T062701_009877_013B0F"
    standard = Scene("00000000-0000-0000-0000-0000000d0001", f"{frame}_5632.SAFE", T0, T0, "DESCENDING", 154, 0,
                     True, box(-7, 35, -5, 37))  # fmt: skip
    cog = Scene("00000000-0000-0000-0000-0000000d0002", f"{frame}_434A_COG.SAFE", T0, T0, "DESCENDING", 154, 0,
                True, box(-7, 35, -5, 37))  # fmt: skip
    try:
        assert [s.scene for s in await store.queue_new(engine, [standard], {standard.id: 0.8}, "estrecho")] == [
            standard
        ]
        assert await store.queue_new(engine, [cog], {cog.id: 0.8}, "estrecho") == []  # published again, 40 min later
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.name.like(f"{frame}%")))


async def test_a_pass_is_announced_once_its_frames_are_done(engine):
    from shapely import box

    from uatu.db.models import RecordedZone, SarDetection, SarScene
    from uatu.sar import store

    first, second, late, old = (uuid.UUID(f"00000000-0000-0000-0000-0000000a{k:04d}") for k in range(4))

    def frame(scene_id: uuid.UUID, k: int, datatake: str = "ANN001", start: datetime = T0) -> dict:
        at = start + timedelta(seconds=25 * k)
        return {
            "id": scene_id, "product_id": scene_id, "zone": "test", "name": f"S1C_ANNOUNCE_{k}_{datatake}",
            "platform": "S1C", "datatake": datatake,
            "orbit_direction": "ASCENDING", "relative_orbit": 147, "acquired_from": at,
            "acquired_to": at + timedelta(seconds=25), "footprint": f"SRID=4326;{box(-6.0, 35.5, -5.0, 36.5).wkt}",
            "aoi_coverage": 0.4, "status": "queued",
        }  # fmt: skip

    async def set_status(scene_id: uuid.UUID, status: str) -> None:
        async with engine.begin() as conn:
            await conn.execute(update(SarScene).where(SarScene.id == scene_id).values(status=status))

    try:
        async with engine.begin() as conn:
            await conn.execute(insert(RecordedZone).values(key="test", since=T0 - timedelta(days=1)))
            await conn.execute(
                insert(SarScene),
                [frame(first, 0), frame(second, 1), frame(old, 0, "ANN000", datetime(2000, 1, 1, tzinfo=UTC))],
            )
            matched = {"scene_id": first, "seen_at": T0, "lat": 36.0, "lon": -5.5, "status": "matched", "pixels": 20,
                       "length_m": 120.0, "peak_db": 15.0, "vv_db": 10.0}  # fmt: skip
            await conn.execute(insert(SarDetection), [matched])

        await set_status(first, "processed")
        assert await store.pass_announcement(engine, first) is None  # its other frame is still queued

        await set_status(second, "failed")  # the last frame done: the pass, with what worked
        announcement = await store.pass_announcement(engine, second)
        assert announcement == store.Announcement([first], [first], late=False)
        report = await store.pass_report(engine, announcement.frames)
        assert (report.frames, report.detections, report.matched) == (1, 1, 1)
        assert report.coverage == pytest.approx(0.4)

        await store.mark_notified(engine, announcement.fresh)
        assert await store.pass_announcement(engine, first) is None  # announced already

        async with engine.begin() as conn:
            await conn.execute(insert(SarScene), [frame(late, 2) | {"status": "processed"}])
        assert await store.pass_announcement(engine, late) == store.Announcement([late], [late], late=True)

        both = await store.pass_report(engine, [first, late])
        assert (both.frames, both.name) == (2, "S1C_ANNOUNCE_0_ANN001")
        assert both.coverage == pytest.approx(0.8)

        await set_status(old, "processed")
        assert await store.pass_announcement(engine, old) is None  # before AIS was recorded
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.id.in_([first, second, late, old])))
            await conn.execute(delete(RecordedZone).where(RecordedZone.key == "test"))
