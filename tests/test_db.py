"""Integration tests against a real, migrated PostGIS database.

Run with UATU_TEST_DATABASE_URL set, e.g. against the docker compose database. They only
touch rows for their own MMSIs and session ids, and delete them afterwards.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

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
