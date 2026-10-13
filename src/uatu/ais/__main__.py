"""Look into the AIS record by hand.

uatu-ais around LAT LON [--at TIME]   who was near a place around a time (after an incident at sea)
uatu-ais events [--hours 24]          recent AIS events
uatu-ais analyse                      run the detectors now
uatu-ais refresh                      reload sanctioned vessels and infrastructure
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from uatu import db
from uatu.ais import analysis, reference, store
from uatu.ais.coast import CoastDistance
from uatu.config import STRAIT_OF_GIBRALTAR, BBoxSetting, bbox_polygon
from uatu.log import configure_logging
from uatu.sar.config import SarSettings
from uatu.sar.raster import Grid, land_cache


class AisSettings(SarSettings):
    ais_bbox: BBoxSetting = STRAIT_OF_GIBRALTAR


async def cmd_around(settings: AisSettings, args: argparse.Namespace) -> int:
    at = datetime.fromisoformat(args.at).astimezone(UTC) if args.at else datetime.now(UTC)
    window = timedelta(minutes=args.minutes)
    engine = db.create_engine(settings.database_url)
    try:
        found = await store.around(engine, args.lat, args.lon, at, args.radius_km * 1000, window)
    finally:
        await engine.dispose()

    print(f"Within {args.radius_km:g} km of {args.lat}, {args.lon}, {at:%Y-%m-%d %H:%M} UTC ± {args.minutes} min\n")
    print(f"AIS vessels ({len(found.vessels)}):")
    print(f"  {'mmsi':>9}  {'name':22} {'type':>4} {'len':>4}  {'closest':>8}  {'seen (UTC)':13}  sanctions")
    for v in found.vessels:
        print(
            f"  {v['mmsi']:>9}  {(v['name'] or '?')[:22]:22} {v['ship_type'] or '':>4} {v['length_m'] or '':>4}"
            f"  {v['closest_m'] / 1000:6.1f}km  {v['first']:%H:%M}-{v['last']:%H:%M}    {v['sanctions'] or ''}"
        )
    print(f"\nAIS events ({len(found.events)}):")
    for e in found.events:
        end = f"{e['end_at']:%H:%M}" if e["end_at"] else "ongoing"
        print(f"  [{e['severity']}] {e['kind']:25} mmsi {e['mmsi']}  {e['start_at']:%H:%M}-{end}  {e['details']}")
    print(f"\nSAR detections ({len(found.detections)}):")
    for d in found.detections:
        print(
            f"  {d['seen_at']:%H:%M:%S}  {d['status']:12} ~{d['length_m']:.0f} m  {d['away_m'] / 1000:.1f} km away"
            f"  {'mmsi ' + str(d['mmsi']) if d['mmsi'] else ''}"
        )
    return 0


async def cmd_events(settings: AisSettings, args: argparse.Namespace) -> int:
    engine = db.create_engine(settings.database_url)
    try:
        events = await store.notable_since(engine, datetime.now(UTC) - timedelta(hours=args.hours), args.min_severity)
        names = await store.vessel_names(engine, {e.mmsi for e in events} | {e.other_mmsi or 0 for e in events})
    finally:
        await engine.dispose()
    for e in events:
        title, message, link = analysis.describe(e, names)
        print(f"[{e.severity}] {e.start:%m-%d %H:%M}  {title}\n      {message}\n      {link}")
    print(f"\n{len(events)} events")
    return 0


async def cmd_analyse(settings: AisSettings, args: argparse.Namespace) -> int:
    grid = Grid.covering(bbox_polygon(settings.sar_bbox), 10.0)
    cache = settings.sar_data_dir / "cache"
    coast = CoastDistance(grid, land_cache(grid, cache), cache)
    engine = db.create_engine(settings.database_url)
    try:
        outcome = await analysis.analyse(engine, datetime.now(UTC), settings.ais_bbox, coast)
    finally:
        await engine.dispose()
    print(f"{outcome.tracks} tracks, {outcome.detected} events detected, {len(outcome.new)} new")
    return 0


async def cmd_refresh(settings: AisSettings, args: argparse.Namespace) -> int:
    engine = db.create_engine(settings.database_url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=180)) as http:
            vessels = await reference.refresh_sanctions(engine, http)
            features = await reference.refresh_infrastructure(engine, http)
    finally:
        await engine.dispose()
    print(f"{vessels} sanctioned vessels, {features} infrastructure features")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uatu-ais", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    around = commands.add_parser("around", help="who was near a place around a time")
    around.add_argument("lat", type=float)
    around.add_argument("lon", type=float)
    around.add_argument("--at", help="ISO time, e.g. 2026-10-13T18:25Z (default: now)")
    around.add_argument("--radius-km", type=float, default=10.0)
    around.add_argument("--minutes", type=int, default=60, help="either side of --at (default: 60)")
    around.set_defaults(run=cmd_around)

    events = commands.add_parser("events", help="recent AIS events")
    events.add_argument("--hours", type=float, default=24)
    events.add_argument("--min-severity", type=int, default=2)
    events.set_defaults(run=cmd_events)

    commands.add_parser("analyse", help="run the detectors now").set_defaults(run=cmd_analyse)
    commands.add_parser("refresh", help="reload sanctions and infrastructure").set_defaults(run=cmd_refresh)

    args = parser.parse_args()
    try:
        settings = AisSettings()
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        sys.exit(2)
    configure_logging("WARNING", "console")
    sys.exit(asyncio.run(args.run(settings, args)))


if __name__ == "__main__":
    main()
