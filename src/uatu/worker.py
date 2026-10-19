"""Uatu's background worker, on ArdiQ.

Everything works zone by zone (ANALYSIS_ZONES, see uatu.config.ZONES).

SAR
    sar.discover        every SAR_DISCOVER_MINUTES (10): Sentinel-1 frames over each zone acquired
                        since AIS recording began there; records the new ones and queues them.
    sar.process_scene   download, detect ships on the zone's grid, pair them with the AIS recorded
                        at the time, look for their wakes, store. A frame over two zones is downloaded once.
    sar.backfill        on demand: queue a zone's passes of the N days before AIS recording began
                        there, on a low-priority lane, to learn it (fixed objects) from history.
Operations
    ops.backup          daily at 03:00 UTC: pg_dump of the database, the BACKUP_KEEP newest kept.
    ops.health          every 5 minutes: AIS still arriving, disk space, backups recent. Alerts on
                        changes through ntfy, and pings a dead man's switch if configured.
AIS
    ais.analyse         every 10 minutes: AIS gaps, encounters, loitering, vessels over cables, sanctioned
                        vessels and vessels of sanctioned companies, identity changes, foreign state
                        vessels in Spain's and disputed waters... into ais_events;
                        pushes the serious ones at once. Also where vessels stop (berths, anchorages)
                        and their crossings of the Strait.
    ais.digest          08:00 and 20:00 UTC: a summary notification of the notable events of 12 h.
    ref.sanctions       daily: from OpenSanctions, sanctioned vessels, who owns and manages vessels
                        (sanctions lists, Ukraine's GUR, port state control) and other watchlists.
    ref.infrastructure  weekly: Spanish submarine cables, pipelines, platforms and ports; the World Port Index;
                        Spain's territorial sea and the waters it and another state both claim (Marine Regions).
Reports
    report.weekly       Mondays at 05:00 UTC: the report of the week before (uatu-report), in REPORTS_DIR,
                        announced through ntfy with a link to the dashboard's copy if DASHBOARD_URL is set.

    ardiq run uatu.worker:app
"""

import asyncio
import uuid
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog
from ardiq import Ardiq, ErrorContext, ExecutionContext
from pydantic import Field

from uatu import db, ops
from uatu.ais import analysis as ais_analysis
from uatu.ais import reference, sanctions
from uatu.ais import store as ais_store
from uatu.ais.bathymetry import Bathymetry, load_bathymetry
from uatu.config import ZONES, BBox, ZoneSettings
from uatu.log import configure_logging
from uatu.report.data import MADRID
from uatu.report.write import announcement, write_report
from uatu.sar import store
from uatu.sar.analysis import analyse, write_outputs
from uatu.sar.catalog import search, worth_processing
from uatu.sar.config import SarSettings
from uatu.sar.detect import CfarParams
from uatu.sar.download import CdseAuth, download
from uatu.sar.report import scene_notification
from uatu.zones import Geography, geography


class WorkerSettings(SarSettings, ZoneSettings):
    backup_dir: Path = Path("data/backups")
    backup_keep: int = Field(14, gt=0)
    ntfy_url: str | None = None  # https://ntfy.sh/<an unguessable topic>, to get alerts on a phone
    healthcheck_url: str | None = None  # dead man's switch ping URL, e.g. https://hc-ping.com/<uuid>
    reports_dir: Path = Path("data/reports")
    dashboard_url: str | None = None  # e.g. http://192.168.1.41:8000, to link the weekly report from its notification
    ais_stale_minutes: float = Field(15.0, gt=0)  # alert when no AIS has arrived for this long
    min_free_disk: float = Field(0.1, gt=0, lt=1)


settings = WorkerSettings()
configure_logging(settings.log_level, settings.log_format)
log = structlog.get_logger(component="worker")

# Two tasks at a time (a 4 GB container): two scenes take ~1.7 GB of RAM, or one scene and a
# periodic task (health, AIS analysis), which then never waits for a download. Historical scenes
# go on the "backfill" lane, consumed only when nothing else is waiting. ArdiQ's prefetch counts
# the tasks running too: equal to concurrency, both run and none waits in memory behind a long
# download (12 min) instead of being picked up by a free slot.
# Tasks run on one event loop: the locks below keep two scenes of the same frame (two zones)
# from downloading it twice, and a pass from being announced twice.
app = Ardiq(
    redis_url=settings.redis_url,
    queue_name="uatu",
    priorities=["backfill", "default"],
    default_priority="default",
    concurrency=2,
    prefetch=2,
)
_downloading: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # by product id
_announcing = asyncio.Lock()
LOST_AFTER = timedelta(hours=2)
# Tasks that run every few minutes: one failure (a network blip, a service down for a while)
# fixes itself on the next run, so they alert only when failing this many times in a row.
FAILURES_BEFORE_ALERT = {"sar.discover": 3, "ais.analyse": 3, "ops.health": 3}


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
        cache = settings.sar_data_dir / "cache"
        zones = {}
        for zone in settings.zones:
            geo = await asyncio.to_thread(geography, zone, cache)
            zones[zone.key] = ZoneState(geo, await _bathymetry(http, zone.bbox, cache))
        await _load_reference_if_missing(engine, http)
        log.info("worker_ready", zones=list(zones), alerts=settings.ntfy_url is not None)
        names = ", ".join(z.geography.zone.name for z in zones.values())
        await alerter.notify("Uatu worker started", f"Vigilando: {names}", "low")
        yield {
            "failures": {},  # consecutive failures of periodic tasks
            "engine": engine,
            "http": http,
            "alerter": alerter,
            "auth": auth,
            "zones": zones,
        }


@dataclass(frozen=True)
class ZoneState:
    geography: Geography
    bathymetry: Bathymetry | None


async def _bathymetry(http: httpx.AsyncClient, bbox: BBox, cache: Path) -> Bathymetry | None:
    try:
        return await load_bathymetry(http, bbox, cache)
    except httpx.HTTPError as e:  # without it, every vessel over a cable is an alert
        log.warning("bathymetry_unavailable", bbox=bbox, error=repr(e))
        return None


async def _load_reference_if_missing(engine, http: httpx.AsyncClient) -> None:
    """First start: don't wait for the scheduled refresh to have sanctions, infrastructure, ports and Spain's waters."""
    listed, infrastructure, ports = await ais_store.reference_loaded(engine)
    try:
        if not listed:
            await sanctions.refresh_sanctions(engine, http)
        if not infrastructure:
            await reference.refresh_infrastructure(engine, http)
        if not ports:
            await reference.refresh_ports(engine, http)
        if not await ais_store.maritime_waters(engine):
            await reference.refresh_maritime_areas(engine, http)
    except Exception:  # never keep the worker from starting: the scheduled refresh will try again
        log.exception("reference_load_failed")


# --- SAR -------------------------------------------------------------------------------------------


@app.cron(every=timedelta(minutes=settings.sar_discover_minutes), name="sar.discover", max_retries=0, timeout=300)
async def discover() -> int:
    """Record and queue new scenes, and re-queue any whose task was lost."""
    engine = app.state.engine
    now = datetime.now(UTC)
    found = 0
    for key, state in app.state.zones.items():
        recording_since = await store.recording_start(engine, key)
        if recording_since is None:
            log.info("discover_skipped", zone=key, reason="no AIS recorded there yet")
            continue
        since = max(now - timedelta(days=settings.sar_lookback_days), recording_since)
        # The catalogue can omit products for a few minutes after publishing them: a frame
        # missed now is simply found on a later run.
        new = await _queue(state.geography, since, now)
        for scene in new:
            await _enqueue_scene(scene, recording_since)
        found += len(new)
    # Processing takes minutes and retries within the hour: still queued after that, the task
    # was lost (e.g. Redis wiped). Its id is fixed, so re-queueing never duplicates it.
    lost = await store.queued(engine, discovered_before=now - LOST_AFTER)
    for scene in lost:
        await _enqueue_scene(scene, await store.recording_start(engine, scene.zone))
    log.info("discovered", new=found, requeued=len(lost))
    return found


async def _queue(geo: Geography, start: datetime, end: datetime) -> list[store.ZoneScene]:
    """Record the frames over the zone acquired between start and end that are worth it and new: those
    that cover enough of its sea (much of a zone's box can be land)."""
    scenes = await search(app.state.http, geo.area, start, end)
    candidates = worth_processing(scenes, geo.sea, settings.sar_min_coverage)
    coverage = {s.id: s.coverage(geo.sea) for s in candidates}
    return await store.queue_new(app.state.engine, candidates, coverage, geo.zone.key)


async def _enqueue_scene(scene: store.ZoneScene, recording_since: datetime | None) -> None:
    """Queue a scene by a fixed id (re-queueing never duplicates); historical ones on the backfill lane."""
    lane = "backfill" if recording_since is None or scene.scene.start < recording_since else "default"
    await process_scene.options(task_id=f"sar-scene:{scene.id}", priority=lane).enqueue(str(scene.id))


@app.task(name="sar.backfill", max_retries=0, timeout=900)
async def backfill(days: float, zone: str) -> int:
    """Queue a zone's passes of the `days` before AIS recording began there. Without AIS they are not
    paired, but their detections teach Uatu which objects in the zone never move."""
    state = app.state.zones.get(zone)
    if state is None:
        raise RuntimeError(f"zone {zone!r} is not analysed (ANALYSIS_ZONES)")
    recording_since = await store.recording_start(app.state.engine, zone)
    if recording_since is None:
        raise RuntimeError(f"no AIS recorded in {zone!r} yet: nothing to set the backfill against")
    new = await _queue(state.geography, recording_since - timedelta(days=days), recording_since)
    for scene in new:
        await _enqueue_scene(scene, recording_since)
    log.info("backfill_queued", zone=zone, days=days, queued=len(new))
    return len(new)


@app.task(name="sar.process_scene", max_retries=3, backoff_ms=10 * 60_000, timeout=45 * 60)
async def process_scene(scene_id: str, announce: bool = True) -> dict[str, int]:
    """Download a frame, detect ships, pair them with AIS and store it all; announce its pass once complete
    (not when processed again, see uatu-sar reprocess: its pass was told already)."""
    state = app.state
    if state.auth is None:
        raise RuntimeError("CDSE credentials not configured")
    zs = await store.load(state.engine, uuid.UUID(scene_id))
    zone = state.zones.get(zs.zone)
    if zone is None:
        raise RuntimeError(f"zone {zs.zone!r} is not analysed (ANALYSIS_ZONES)")
    geo, scene = zone.geography, zs.scene
    if scene.coverage(geo.sea) < settings.sar_min_coverage:  # queued before coverage meant sea
        await store.mark_skipped(state.engine, zs.id, "covers too little of the zone's sea")
        log.info("scene_skipped", scene=scene.name, zone=zs.zone, sea_coverage=round(scene.coverage(geo.sea), 3))
        return {"skipped": 1}
    recording_since = await store.recording_start(state.engine, zs.zone)
    historical = recording_since is None or scene.start < recording_since
    log.info("scene_started", scene=scene.name, zone=zs.zone, historical=historical)
    async with _downloading[scene.id]:  # the frame's other zone may be downloading it right now
        path = await download(state.http, state.auth, scene, settings.sar_data_dir)
    engine = None if historical else state.engine  # no AIS to pair with
    out_dir = settings.sar_data_dir / zs.zone / scene.name.removesuffix(".SAFE")
    out_dir.mkdir(parents=True, exist_ok=True)
    image = None if historical else out_dir / "radar_utm.tif"  # map tiles, for the passes shown
    # Wakes take seconds a detection: for the passes watched live, not for history.
    depth_at = zone.bathymetry.depth_at if zone.bathymetry else None
    analysis = await analyse(
        path,
        geo.grid,
        geo.land,
        CfarParams(),
        engine,
        geo.zone.bbox,
        image_path=image,
        wakes=not historical,
        depth_at=depth_at,
    )
    await asyncio.to_thread(write_outputs, analysis, out_dir)
    summary = await store.save_results(state.engine, zs.id, analysis)
    if not settings.sar_keep_scenes and not await store.product_still_needed(state.engine, zs):
        path.unlink(missing_ok=True)
    objects = await store.rebuild_fixed_objects(state.engine, srid=geo.grid.crs.to_epsg())
    log.info("scene_processed", scene=scene.name, zone=zs.zone, historical=historical, **summary, **objects)
    if not historical and announce:
        await announce_pass(uuid.UUID(scene_id))
    return summary


async def announce_pass(scene_id: uuid.UUID) -> None:
    """Notify the pass a frame belongs to, once all its frames are done (see store.pass_announcement).
    A frame that turns up after its pass was announced is notified only if it adds vessels without AIS."""
    engine = app.state.engine
    async with _announcing:  # two frames of a pass finishing together must not both announce it
        announcement = await store.pass_announcement(engine, scene_id)
        if announcement is None:
            return
        report = await store.pass_report(engine, announcement.frames)
        if not announcement.late or any(c.kind != "reception_gap" for c in report.candidates):
            title, message, priority, link = scene_notification(report, late=announcement.late)
            await app.state.alerter.notify(title, message, priority, link)
        await store.mark_notified(engine, announcement.fresh)


# --- AIS ------------------------------------------------------------------------------------------


@app.cron(every=timedelta(minutes=10), name="ais.analyse", max_retries=0, timeout=300)
async def analyse_ais() -> dict[str, int]:
    state = app.state
    now = datetime.now(UTC)
    areas = [
        ais_analysis.Area(z.geography.zone.bbox, z.geography.coast, z.bathymetry, z.geography.zone.key)
        for z in state.zones.values()
    ]
    outcome = await ais_analysis.analyse(state.engine, now, areas)
    urgent = [e for e in outcome.new if e.severity >= 3 and ais_analysis.worth_alerting(e, now)]
    if urgent:
        mmsis = {e.mmsi for e in urgent} | {e.other_mmsi for e in urgent if e.other_mmsi}
        names = await ais_store.vessel_names(state.engine, mmsis)
        for e in urgent[:5]:
            title, message, link = ais_analysis.describe(e, names)
            await state.alerter.notify(title, message, "high", link)
        if len(urgent) > 5:
            await state.alerter.notify("Uatu: más alertas AIS", f"{len(urgent) - 5} alertas más en ais_events", "high")
    log.info(
        "ais_analysed",
        tracks=outcome.tracks,
        detected=outcome.detected,
        new=len(outcome.new),
        urgent=len(urgent),
        gaps_closed=outcome.gaps_closed,
        stops=outcome.stops,
        transits=outcome.transits,
    )
    return {"new": len(outcome.new), "urgent": len(urgent)}


@app.cron("0 8,20 * * *", name="ais.digest", max_retries=0, timeout=120)
async def ais_digest() -> int:
    """Notable events go into this twice-daily summary rather than one notification each."""
    state = app.state
    events = await ais_store.notable_since(state.engine, datetime.now(UTC) - timedelta(hours=12), min_severity=2)
    if events:
        names = await ais_store.vessel_names(
            state.engine, {e.mmsi for e in events} | {e.other_mmsi or 0 for e in events}
        )
        title, message = ais_analysis.digest(events, names, hours=12)
        await state.alerter.notify(title, message, "default")
    return len(events)


@app.cron("0 5 * * 1", name="report.weekly", max_retries=1, backoff_ms=30 * 60_000, timeout=900)
async def weekly_report() -> str:
    """Monday morning: the report of the week before, Monday to Sunday in Spanish time."""
    state = app.state
    until = datetime.now(MADRID).date()
    folder, r = await write_report(
        state.engine, until, 7, settings.zones, ZONES, settings.sar_data_dir, settings.reports_dir
    )
    title, message, link = announcement(folder, r, settings.dashboard_url)
    await state.alerter.notify(title, message, "default", link)
    log.info("report_written", folder=str(folder))
    return str(folder)


@app.cron("0 4 * * *", name="ref.sanctions", max_retries=2, backoff_ms=30 * 60_000, timeout=900)
async def refresh_sanctions() -> int:
    return len((await sanctions.refresh_sanctions(app.state.engine, app.state.http)).sanctioned)


@app.cron("30 4 * * 1", name="ref.infrastructure", max_retries=2, backoff_ms=30 * 60_000, timeout=900)
async def refresh_infrastructure() -> int:
    await reference.refresh_ports(app.state.engine, app.state.http)
    await reference.refresh_maritime_areas(app.state.engine, app.state.http)
    return await reference.refresh_infrastructure(app.state.engine, app.state.http)


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
    if ctx.name in FAILURES_BEFORE_ALERT:
        failures = app.state.failures
        failures[ctx.name] = count = failures.get(ctx.name, 0) + 1
        if count >= FAILURES_BEFORE_ALERT[ctx.name]:
            detail = f"failed {count} times in a row, last: {ctx.exc!r}"
            await app.state.alerter.update([ops.Check(ctx.name, False, detail)])
        return
    if ctx.name == "sar.process_scene" and ctx.task_id.startswith("sar-scene:"):
        scene_id = uuid.UUID(ctx.task_id.removeprefix("sar-scene:"))
        await store.mark_failed(app.state.engine, scene_id, repr(ctx.exc))
        await announce_pass(scene_id)  # the frames of its pass that did work
    await app.state.alerter.notify(f"Uatu: {ctx.name} failed", f"after {ctx.tries} tries: {ctx.exc!r}", "high")


@app.middleware
async def track_periodic(ctx: ExecutionContext, call_next):
    """A periodic task that ran fine: forget its failures, and say so if they had been alerted."""
    result = await call_next()
    if ctx.name in FAILURES_BEFORE_ALERT and app.state.failures.pop(ctx.name, 0):
        await app.state.alerter.update([ops.Check(ctx.name, True, "running again")])
    return result
