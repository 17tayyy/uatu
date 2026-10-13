"""Uatu's background worker, on ArdiQ.

SAR
    sar.discover        every SAR_DISCOVER_MINUTES (10): Sentinel-1 frames over the area acquired
                        since AIS recording began; records the new ones and queues them.
    sar.process_scene   download, detect ships, pair them with the AIS recorded at the time, store.
Operations
    ops.backup          daily at 03:00 UTC: pg_dump of the database, the BACKUP_KEEP newest kept.
    ops.health          every 5 minutes: AIS still arriving, disk space, backups recent. Alerts on
                        changes through ntfy, and pings a dead man's switch if configured.

    ardiq run uatu.worker:app
"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog
from ardiq import Ardiq, ErrorContext
from pydantic import Field

from uatu import db, ops
from uatu.config import bbox_polygon
from uatu.log import configure_logging
from uatu.sar import store
from uatu.sar.analysis import analyse, write_outputs
from uatu.sar.catalog import search, worth_processing
from uatu.sar.config import SarSettings
from uatu.sar.detect import CfarParams
from uatu.sar.download import CdseAuth, download
from uatu.sar.raster import Grid, land_cache


class WorkerSettings(SarSettings):
    backup_dir: Path = Path("data/backups")
    backup_keep: int = Field(14, gt=0)
    ntfy_url: str | None = None  # https://ntfy.sh/<an unguessable topic>, to get alerts on a phone
    healthcheck_url: str | None = None  # dead man's switch ping URL, e.g. https://hc-ping.com/<uuid>
    ais_stale_minutes: float = Field(15.0, gt=0)  # alert when no AIS has arrived for this long
    min_free_disk: float = Field(0.1, gt=0, lt=1)


settings = WorkerSettings()
configure_logging(settings.log_level, settings.log_format)
log = structlog.get_logger(component="worker")

# One task at a time: processing a scene takes ~0.8 GB of RAM.
app = Ardiq(redis_url=settings.redis_url, queue_name="uatu", concurrency=1)
AOI = bbox_polygon(settings.sar_bbox)
LOST_AFTER = timedelta(hours=2)


@app.lifespan
async def lifespan():
    async with AsyncExitStack() as stack:
        engine = db.create_engine(settings.database_url)
        stack.push_async_callback(engine.dispose)
        await db.wait_until_ready(engine)
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)))
        alerter = ops.Alerter(ops.ntfy(http, settings.ntfy_url))
        auth = None
        if settings.cdse_username and settings.cdse_password:
            auth = CdseAuth(http, settings.cdse_username, settings.cdse_password.get_secret_value())
        else:
            log.warning("cdse_credentials_missing", hint="set CDSE_USERNAME and CDSE_PASSWORD to download scenes")
        grid = Grid.covering(AOI, 10.0)
        land = await asyncio.to_thread(land_cache, grid, settings.sar_data_dir / "cache")
        log.info("worker_ready", grid=f"{grid.width}x{grid.height}", alerts=settings.ntfy_url is not None)
        await alerter.notify("Uatu worker started", f"Watching {settings.sar_bbox}", "low")
        yield {"engine": engine, "http": http, "alerter": alerter, "auth": auth, "grid": grid, "land": land}


# --- SAR -------------------------------------------------------------------------------------------


@app.cron(every=timedelta(minutes=settings.sar_discover_minutes), name="sar.discover", max_retries=0, timeout=300)
async def discover() -> int:
    """Record and queue new scenes, and re-queue any whose task was lost."""
    engine = app.state.engine
    recording_since = await store.ais_recording_start(engine)
    if recording_since is None:
        log.info("discover_skipped", reason="no AIS recorded yet")
        return 0
    now = datetime.now(UTC)
    since = max(now - timedelta(days=settings.sar_lookback_days), recording_since)
    # The catalogue can omit products for a few minutes after publishing them: a frame
    # missed now is simply found on a later run.
    scenes = await search(app.state.http, AOI, since, now)
    candidates = worth_processing(scenes, AOI, settings.sar_min_coverage)
    new = await store.queue_new(engine, candidates, {s.id: s.coverage(AOI) for s in candidates})
    # Processing takes minutes and retries within the hour: still queued after that, the task
    # was lost (e.g. Redis wiped). Its id is fixed, so re-queueing never duplicates it.
    lost = await store.queued(engine, discovered_before=now - LOST_AFTER)
    for scene in [*new, *lost]:
        await process_scene.options(task_id=f"sar-scene:{scene.id}").enqueue(scene.id)
    log.info("discovered", catalogue=len(scenes), new=len(new), requeued=len(lost))
    return len(new)


@app.task(name="sar.process_scene", max_retries=3, backoff_ms=10 * 60_000, timeout=45 * 60)
async def process_scene(scene_id: str) -> dict[str, int]:
    state = app.state
    if state.auth is None:
        raise RuntimeError("CDSE credentials not configured")
    scene = await store.load(state.engine, uuid.UUID(scene_id))
    log.info("scene_started", scene=scene.name)
    path = await download(state.http, state.auth, scene, settings.sar_data_dir)
    analysis = await analyse(path, state.grid, state.land, CfarParams(), state.engine, settings.sar_bbox)
    await asyncio.to_thread(write_outputs, analysis, settings.sar_data_dir / analysis.name)
    summary = await store.save_results(state.engine, uuid.UUID(scene_id), analysis)
    if not settings.sar_keep_scenes:
        path.unlink(missing_ok=True)
    log.info("scene_processed", scene=scene.name, **summary)
    return summary


# --- operations ------------------------------------------------------------------------------------


@app.cron("0 3 * * *", name="ops.backup", max_retries=2, backoff_ms=10 * 60_000, timeout=30 * 60)
async def backup() -> str:
    path = await ops.backup_database(settings.database_url, settings.backup_dir, settings.backup_keep)
    log.info("backup_done", path=str(path), mb=round(path.stat().st_size / 2**20, 1))
    return path.name


@app.cron(every=timedelta(minutes=5), name="ops.health", max_retries=0, timeout=120)
async def health() -> dict[str, bool]:
    checks = [
        await ops.check_ais(app.state.engine, timedelta(minutes=settings.ais_stale_minutes)),
        ops.check_disk(settings.sar_data_dir, settings.min_free_disk),
        ops.check_backup(settings.backup_dir, timedelta(hours=26)),
    ]
    await app.state.alerter.update(checks)
    if settings.healthcheck_url:
        await ops.ping_healthcheck(app.state.http, settings.healthcheck_url, all(c.ok for c in checks))
    return {c.name: c.ok for c in checks}


@app.on_error
async def on_error(ctx: ErrorContext) -> None:
    log.error(
        "task_failed",
        task=ctx.name,
        task_id=ctx.task_id,
        tries=ctx.tries,
        will_retry=ctx.will_retry,
        error=repr(ctx.exc),
    )
    if ctx.will_retry:
        return
    if ctx.name == "sar.process_scene" and ctx.task_id.startswith("sar-scene:"):
        scene_id = uuid.UUID(ctx.task_id.removeprefix("sar-scene:"))
        await store.mark_failed(app.state.engine, scene_id, repr(ctx.exc))
    await app.state.alerter.notify(f"Uatu: {ctx.name} failed", f"after {ctx.tries} tries: {ctx.exc!r}", "high")
