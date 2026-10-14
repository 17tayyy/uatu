"""Entry point: `uatu-recorder` or `python -m uatu.recorder`."""

import asyncio
import signal
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu import db
from uatu.db.models import RecordedZone
from uatu.log import configure_logging
from uatu.recorder.config import RecorderSettings
from uatu.recorder.state import Stats, report_stats
from uatu.recorder.stream import AisStream
from uatu.recorder.writer import DbWriter, RawArchive

log = structlog.get_logger(component="recorder")


async def run(settings: RecorderSettings) -> int:
    log.info(
        "starting",
        zones=settings.zones,
        raw_archive_dir=str(settings.raw_archive_dir) if settings.raw_archive_dir else None,
    )
    engine = db.create_engine(settings.database_url)
    try:
        revision = await db.wait_until_ready(engine)
        log.info("db_ready", schema_revision=revision)
        await register_zones(engine, settings.zones)
        return await record(settings, engine)
    finally:
        await engine.dispose()


async def register_zones(engine: AsyncEngine, keys: list[str]) -> None:
    """Note since when each zone is recorded: radar passes before that have no AIS to pair with."""
    rows = [{"key": key, "since": datetime.now(UTC)} for key in keys]
    async with engine.begin() as conn:
        await conn.execute(pg_insert(RecordedZone).on_conflict_do_nothing(index_elements=[RecordedZone.key]), rows)


async def record(settings: RecorderSettings, engine: AsyncEngine) -> int:
    stats = Stats()
    batching = {
        "batch_size": settings.batch_size,
        "flush_interval_s": settings.flush_interval_s,
        "queue_max": settings.queue_max,
    }
    db_writer = DbWriter(engine, stats, **batching)
    archive = None
    if settings.raw_archive_dir is not None:
        archive = RawArchive(settings.raw_archive_dir, stats, **(batching | {"flush_interval_s": 10.0}))
    workers = [w for w in (db_writer, archive) if w is not None]
    for worker in workers:
        worker.start()

    stream_task = asyncio.create_task(AisStream(settings, db_writer, archive, stats).run(), name="stream")
    stats_task = asyncio.create_task(report_stats(stats, workers, settings.stats_interval_s), name="stats")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stop_task = asyncio.create_task(stop.wait(), name="stop")

    done, _ = await asyncio.wait(
        {stop_task, stream_task, *(w.task for w in workers)}, return_when=asyncio.FIRST_COMPLETED
    )
    crashed = [t for t in done if t is not stop_task]
    for task in crashed:
        log.error("task_crashed", task=task.get_name(), exc_info=task.exception())

    log.info("shutting_down")
    stream_task.cancel()
    await asyncio.gather(stream_task, return_exceptions=True)
    for worker in workers:  # the stream is stopped, so these drain whatever is left
        await worker.close(settings.shutdown_timeout_s)
    stats_task.cancel()
    stop_task.cancel()
    log.info("stopped", **asdict(stats))
    return 1 if crashed else 0


def main() -> None:
    try:
        settings = RecorderSettings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        sys.exit(2)
    configure_logging(settings.log_level, settings.log_format)
    try:
        sys.exit(asyncio.run(run(settings)))
    except KeyboardInterrupt:  # Ctrl-C before the signal handlers were installed
        sys.exit(130)


if __name__ == "__main__":
    main()
