"""Background SAR processing on ArdiQ.

Every SAR_DISCOVER_MINUTES (10) `discover` looks for Sentinel-1 frames over the area acquired since AIS
recording began, records the new ones and queues them; `process_scene` downloads each,
detects ships, pairs them with the AIS recorded at the time and stores the results.

    ardiq run uatu.sar.worker:app
"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from ardiq import Ardiq, ErrorContext
from shapely import Geometry

from uatu import db
from uatu.config import bbox_polygon
from uatu.log import configure_logging
from uatu.sar import store
from uatu.sar.analysis import analyse, write_outputs
from uatu.sar.catalog import Scene, search
from uatu.sar.config import SarSettings
from uatu.sar.detect import CfarParams
from uatu.sar.download import CdseAuth, download
from uatu.sar.raster import Grid, land_cache

settings = SarSettings()
configure_logging(settings.log_level, settings.log_format)
log = structlog.get_logger(component="sar_worker")

# One task at a time: processing a scene takes ~0.75 GB of RAM.
app = Ardiq(redis_url=settings.redis_url, queue_name="uatu-sar", concurrency=1)
AOI = bbox_polygon(settings.sar_bbox)
LOST_AFTER = timedelta(hours=2)


@app.lifespan
async def lifespan():
    async with AsyncExitStack() as stack:
        engine = db.create_engine(settings.database_url)
        stack.push_async_callback(engine.dispose)
        await db.wait_until_ready(engine)
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=httpx.Timeout(30, read=120)))
        auth = None
        if settings.cdse_username and settings.cdse_password:
            auth = CdseAuth(http, settings.cdse_username, settings.cdse_password.get_secret_value())
        else:
            log.warning("cdse_credentials_missing", hint="set CDSE_USERNAME and CDSE_PASSWORD to download scenes")
        grid = Grid.covering(AOI, 10.0)
        land = await asyncio.to_thread(land_cache, grid, settings.sar_data_dir / "cache")
        log.info("worker_ready", grid=f"{grid.width}x{grid.height}", data_dir=str(settings.sar_data_dir))
        yield {"engine": engine, "http": http, "auth": auth, "grid": grid, "land": land}


def worth_processing(scenes: list[Scene], aoi: Geometry, min_coverage: float) -> list[Scene]:
    """Scenes covering enough of the area: a frame that grazes it costs a 1-2 GB download for little."""
    return [s for s in scenes if s.coverage(aoi) >= min_coverage]


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
    if ctx.name == "sar.process_scene" and not ctx.will_retry and ctx.task_id.startswith("sar-scene:"):
        scene_id = uuid.UUID(ctx.task_id.removeprefix("sar-scene:"))
        await store.mark_failed(app.state.engine, scene_id, repr(ctx.exc))
