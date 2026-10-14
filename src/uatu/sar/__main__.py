"""Work with Sentinel-1 scenes by hand.

uatu-sar search [--days 7]               passes over the area of interest
uatu-sar download (--latest | DATATAKE)  download every frame of a pass
uatu-sar detect SCENE.zip [--ais]        detect ships, optionally pairing them with recorded AIS
uatu-sar backfill ZONE [--days 90]       have the worker process a zone's passes before AIS recording began there
uatu-sar candidates [--days 7]           dark vessel candidates of recent passes
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import shapely
import structlog
from pydantic import ValidationError

from uatu import db
from uatu.config import ZONES, bbox_polygon
from uatu.log import configure_logging
from uatu.sar.analysis import PairingParams, analyse, write_outputs
from uatu.sar.catalog import group_passes, search
from uatu.sar.config import SarSettings
from uatu.sar.detect import CfarParams
from uatu.sar.download import CdseAuth, download
from uatu.sar.raster import Grid, land_cache

log = structlog.get_logger(component="sar")


async def find_passes(settings: SarSettings, days: float):
    aoi = bbox_polygon(settings.sar_bbox)
    until = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=30) as client:
        scenes = await search(client, aoi, until - timedelta(days=days), until)
    return aoi, group_passes(scenes)


async def cmd_search(settings: SarSettings, args: argparse.Namespace) -> int:
    aoi, passes = await find_passes(settings, args.days)
    print(f"{'pass':8} {'start (UTC)':17} {'sat':4} {'direction':11} {'orbit':>5} {'frames':>6} {'AOI':>5} {'size':>7}")
    for datatake, frames in passes.items():
        first = frames[0]
        covered = shapely.union_all([f.footprint for f in frames]).intersection(aoi).area / aoi.area
        size_gb = sum(f.size_bytes for f in frames) / 1e9
        print(
            f"{datatake:8} {first.start:%Y-%m-%d %H:%M} {first.platform:4} {first.orbit_direction:11}"
            f" {first.relative_orbit:5} {len(frames):6} {covered:5.0%} {size_gb:5.1f}GB"
        )
    return 0


async def cmd_download(settings: SarSettings, args: argparse.Namespace) -> int:
    if settings.cdse_username is None or settings.cdse_password is None:
        print("set CDSE_USERNAME and CDSE_PASSWORD (Copernicus Data Space account)", file=sys.stderr)
        return 2
    aoi, passes = await find_passes(settings, args.days)
    if not passes:
        print("no passes found", file=sys.stderr)
        return 1
    datatake = list(passes)[-1] if args.latest else args.datatake
    if datatake not in passes:
        print(f"pass {datatake} not found in the last {args.days:g} days", file=sys.stderr)
        return 1

    timeout = httpx.Timeout(30, read=120)
    async with httpx.AsyncClient(timeout=timeout) as client:
        auth = CdseAuth(client, settings.cdse_username, settings.cdse_password.get_secret_value())
        for scene in passes[datatake]:
            if scene.coverage(aoi) < args.min_coverage:
                print(f"skipping {scene.name}: covers {scene.coverage(aoi):.0%} of the area", file=sys.stderr)
                continue
            path = await download(client, auth, scene, settings.sar_data_dir)
            print(path)
    return 0


async def cmd_detect(settings: SarSettings, args: argparse.Namespace) -> int:
    scene = Path(args.scene)
    grid = Grid.covering(bbox_polygon(settings.sar_bbox), args.resolution)
    pairing = PairingParams(
        base_tolerance_m=args.max_distance, min_ais_nearby=args.min_ais_nearby, doppler_sign=args.doppler_sign
    )
    log.info("processing", scene=scene.name, grid=f"{grid.width}x{grid.height} @ {grid.resolution:g} m")
    land = land_cache(grid, settings.sar_data_dir / "cache")
    engine = db.create_engine(settings.database_url) if args.ais else None
    try:
        analysis = await analyse(scene, grid, land, CfarParams(), engine, settings.sar_bbox, pairing)
    finally:
        if engine is not None:
            await engine.dispose()

    log.info("detected", ships=len(analysis.output.detections))
    if (result := analysis.result) is not None:
        distances = sorted(m.distance_m for m in result.matches)
        log.info(
            "matched",
            ais_vessels=analysis.ais_vessels,
            matched=len(result.matches),
            dark=len(result.dark),
            no_coverage=len(result.no_coverage),
            undetected=len(result.undetected),
            not_imaged=len(result.not_imaged),
            median_distance_m=distances[len(distances) // 2] if distances else None,
        )
    out_dir = settings.sar_data_dir / analysis.name
    write_outputs(analysis, out_dir)
    print(out_dir)
    return 0


async def cmd_backfill(settings: SarSettings, args: argparse.Namespace) -> int:
    from uatu.worker import backfill  # the worker's app: same Redis and queue

    job = await backfill.enqueue(args.days, args.zone)
    result = await job.result(timeout=900)
    if not result.success:
        print(f"backfill failed: {result.value}", file=sys.stderr)
        return 1
    print(f"{result.value} scenes of {args.zone} queued on the backfill lane")
    return 0


async def cmd_candidates(settings: SarSettings, args: argparse.Namespace) -> int:
    from uatu.sar import store
    from uatu.sar.report import scene_notification

    engine = db.create_engine(settings.database_url)
    try:
        for frames in await store.recent_live_passes(engine, datetime.now(UTC) - timedelta(days=args.days)):
            title, message, _, link = scene_notification(await store.pass_report(engine, frames))
            print(f"{title}\n{message}" + (f"\n{link}" if link else "") + "\n")
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uatu-sar", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    search_cmd = commands.add_parser("search", help="list Sentinel-1 passes over the area of interest")
    search_cmd.add_argument("--days", type=float, default=7, help="how far back to look (default: 7)")
    search_cmd.set_defaults(run=cmd_search)

    download_cmd = commands.add_parser("download", help="download every frame of a pass")
    which = download_cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("datatake", nargs="?", help="pass id, as listed by `search`")
    which.add_argument("--latest", action="store_true", help="the most recent pass")
    download_cmd.add_argument("--days", type=float, default=14, help="how far back to look (default: 14)")
    download_cmd.add_argument(
        "--min-coverage", type=float, default=0.1, help="skip frames covering less of the area (default: 0.1)"
    )
    download_cmd.set_defaults(run=cmd_download)

    detect_cmd = commands.add_parser("detect", help="detect ships in a downloaded scene")
    detect_cmd.add_argument("scene", help="zipped SAFE, as saved by `download`")
    detect_cmd.add_argument("--ais", action="store_true", help="pair detections with AIS from DATABASE_URL")
    detect_cmd.add_argument("--resolution", type=float, default=10.0, help="grid pixel size in metres (default: 10)")
    detect_cmd.add_argument(
        "--max-distance",
        type=float,
        default=1000.0,
        help="detection-AIS distance tolerated for a vessel with fresh AIS, metres (default: 1000)",
    )
    detect_cmd.add_argument(
        "--min-ais-nearby",
        type=int,
        default=5,
        help="AIS vessels heard within 15 km and 2 h for an unpaired detection to count as dark (default: 5)",
    )
    detect_cmd.add_argument("--doppler-sign", type=float, default=1.0, help=argparse.SUPPRESS)
    detect_cmd.set_defaults(run=cmd_detect)

    backfill_cmd = commands.add_parser("backfill", help="queue historical passes for the worker")
    backfill_cmd.add_argument("zone", choices=list(ZONES), help="zone key, see uatu.config.ZONES")
    backfill_cmd.add_argument("--days", type=float, default=90, help="before AIS recording began (default: 90)")
    backfill_cmd.set_defaults(run=cmd_backfill)

    candidates_cmd = commands.add_parser("candidates", help="dark vessel candidates of recent passes")
    candidates_cmd.add_argument("--days", type=float, default=7)
    candidates_cmd.set_defaults(run=cmd_candidates)

    args = parser.parse_args()
    try:
        settings = SarSettings()
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        sys.exit(2)
    configure_logging("INFO", "console")
    sys.exit(asyncio.run(args.run(settings, args)))


if __name__ == "__main__":
    main()
