"""Integration tests against a real, migrated PostGIS database.

Run with UATU_TEST_DATABASE_URL set to a database no recorder is writing to, e.g. the local
docker compose one. They only touch their own rows and delete them afterwards.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select

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
    from uatu.sar.ais import AisVessel
    from uatu.sar.analysis import Analysis
    from uatu.sar.annotation import AcquisitionGeometry
    from uatu.sar.catalog import Scene
    from uatu.sar.detect import Detection
    from uatu.sar.match import Expected, Match, MatchResult
    from uatu.sar.pipeline import SceneOutput
    from uatu.sar.raster import Grid

    scene_id = "00000000-0000-0000-0000-00000000beef"
    name = "S1C_IW_GRDH_1SDV_20261013T182543_20261013T182608_009870_013AD6_TEST.SAFE"
    scene = Scene(scene_id, name, T0, T0 + timedelta(seconds=25), "ASCENDING", 147, 0, True, box(-7, 35, -5, 37))
    try:
        assert await store.queue_new(engine, [scene], {scene_id: 0.42}) == [scene]
        assert await store.queue_new(engine, [scene], {scene_id: 0.42}) == []  # already known
        assert [s.id for s in await store.queued(engine)] == [scene_id]
        loaded = await store.load(engine, uuid.UUID(scene_id))
        assert loaded.name == scene.name and loaded.footprint.equals(scene.footprint)

        def det(lon: float, lat: float) -> Detection:
            return Detection(lon, lat, 0, 0, 20, 150, 20, 10, -8, -20)

        matched, dark, uncovered = det(-5.6, 36.0), det(-5.5, 36.0), det(-6.3, 36.1)
        vessel = AisVessel(MMSI_A, "TEST", 70, 150, -5.6, 36.0, 12.0, 90.0, T0, 30.0)
        expected = Expected(vessel, 0, 0, -120.0, (0, 0), 1000.0)
        result = MatchResult([Match(matched, expected, 48.0)], [dark], [], [], [uncovered], {dark: 40, uncovered: 0})
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
        assert sorted((d.status, d.mmsi, d.ais_vessels_nearby) for d in detections) == [
            ("dark", None, 40), ("matched", MMSI_A, None), ("no_coverage", None, 0)
        ]  # fmt: skip
        assert [(c.mmsi, c.status) for c in checks] == [(MMSI_A, "detected")]
        assert all(T0 <= d.seen_at <= T0 + timedelta(seconds=25) for d in detections)

        await store.mark_failed(engine, uuid.UUID(scene_id), "boom")
        assert await store.queued(engine) == []
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.id == uuid.UUID(scene_id)))


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
