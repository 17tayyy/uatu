"""Look into the AIS record by hand.

uatu-ais around LAT LON [--at TIME]   who was near a place around a time (after an incident at sea)
uatu-ais events [--hours 24]          recent AIS events
uatu-ais analyse                      run the detectors now
uatu-ais stops [--days 7] [--listed]  where vessels stopped (berths, anchorages), sanctioned/watchlisted only
uatu-ais stops --rebuild --days 3     find them again in the recorded AIS of the last days
uatu-ais refresh                      reload sanctions, the companies behind vessels, infrastructure, ports
uatu-ais watch add [--imo N] [--mmsi N] [--name NAME] --note WHY   be told whenever it is heard
uatu-ais watch list | watch remove ID your own watch list
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from uatu import db
from uatu.ais import analysis, reference, sanctions, store
from uatu.ais.events import LocalProjection
from uatu.ais.stops import detect_stops, detect_transits
from uatu.config import ZoneSettings
from uatu.log import configure_logging
from uatu.sar.config import SarSettings
from uatu.zones import geography


class AisSettings(SarSettings, ZoneSettings):
    pass


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
    cache = settings.sar_data_dir / "cache"
    areas = [analysis.Area(z.bbox, geography(z, cache).coast, zone=z.key) for z in settings.zones]
    engine = db.create_engine(settings.database_url)
    try:
        outcome = await analysis.analyse(engine, datetime.now(UTC), areas)
    finally:
        await engine.dispose()
    print(f"{outcome.tracks} tracks, {outcome.detected} events detected, {len(outcome.new)} new")
    print(f"{outcome.stops} vessels stopped, {outcome.transits} new crossings of the Strait")
    return 0


async def cmd_stops(settings: AisSettings, args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    engine = db.create_engine(settings.database_url)
    try:
        if args.rebuild:
            await store.clear_stops(engine, since)
            ports = await store.ports(engine)
            cache = settings.sar_data_dir / "cache"
            for zone in settings.zones:
                lat_min, lon_min, lat_max, lon_max = zone.bbox
                proj = LocalProjection((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
                tracks = await store.tracks(engine, since, now, zone.bbox)
                found = detect_stops(tracks, geography(zone, cache).coast.at, ports, proj)
                stops = await store.save_stops(engine, found, zone.key)
                await store.save(engine, await analysis.listed_stops(engine, stops))
                crossed = await store.save_transits(engine, detect_transits(tracks))
                print(f"{zone.name}: {len(tracks)} tracks, {len(stops)} stops, {crossed} crossings of the Strait")
        rows = await store.stops_since(engine, since, listed_only=args.listed)
    finally:
        await engine.dispose()
    kinds = {"berth": "puerto", "anchorage": "fondeo", "drifting": "a la deriva"}
    for r in rows:
        place = f"{r.port} ({r.port_km:.0f} km)" if r.port else f"{r.lat:.4f}, {r.lon:.4f}"
        hours = (r.end_at - r.start_at).total_seconds() / 3600
        print(
            f"{r.start_at:%m-%d %H:%M}  {hours:5.1f} h  {kinds[r.kind]:11} {(r.name or str(r.mmsi))[:22]:22}"
            f" {place[:32]:32} {r.listed or ''}"
        )
    print(f"\n{len(rows)} stops")
    return 0


async def cmd_refresh(settings: AisSettings, args: argparse.Namespace) -> int:
    engine = db.create_engine(settings.database_url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=180)) as http:
            listed = await sanctions.refresh_sanctions(engine, http)
            features = await reference.refresh_infrastructure(engine, http)
    finally:
        await engine.dispose()
    print(
        f"{len(listed.sanctioned)} sanctioned vessels; {len(listed.companies)} companies behind"
        f" {len({link['vessel_imo'] for link in listed.links})} vessels; {len(listed.watchlists)} on watchlists;"
        f" {len(listed.risks)} off the sanctions lists worth a look; {features} infrastructure features"
    )
    return 0


async def cmd_watch(settings: AisSettings, args: argparse.Namespace) -> int:
    engine = db.create_engine(settings.database_url)
    try:
        if args.action == "add":
            name = args.name.strip() if args.name else None
            if args.imo is None and args.mmsi is None and not name:
                print("give an --imo, an --mmsi or a --name", file=sys.stderr)
                return 2
            watch_id = await store.add_watch(engine, args.note.strip(), args.imo, args.mmsi, name)
            print(f"watching #{watch_id}")
        elif args.action == "remove":
            if not await store.remove_watch(engine, args.id):
                print(f"no watch #{args.id}", file=sys.stderr)
                return 1
            print(f"removed #{args.id}")
        else:
            rows = await store.watches(engine)
            for w in rows:
                who = ", ".join(f"{k} {v}" for k, v in (("IMO", w.imo), ("MMSI", w.mmsi), ("name", w.name)) if v)
                print(f"#{w.id:<4} {who:<50} {w.note}  (since {w.added_at:%Y-%m-%d})")
            print(f"\n{len(rows)} watched")
    finally:
        await engine.dispose()
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
    stops = commands.add_parser("stops", help="where vessels stopped")
    stops.add_argument("--days", type=float, default=7)
    stops.add_argument("--listed", action="store_true", help="sanctioned and watchlisted vessels only")
    stops.add_argument("--rebuild", action="store_true", help="find them again in the recorded AIS")
    stops.set_defaults(run=cmd_stops)
    refresh = commands.add_parser("refresh", help="reload sanctions, ship owners and managers, infrastructure")
    refresh.set_defaults(run=cmd_refresh)

    watch = commands.add_parser("watch", help="your own watch list: an alert whenever one is heard")
    actions = watch.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="watch a vessel by IMO, MMSI or name (any that matches)")
    add.add_argument("--imo", type=int)
    add.add_argument("--mmsi", type=int)
    add.add_argument("--name", help="as broadcast, in any case")
    add.add_argument("--note", required=True, help="why you watch it")
    actions.add_parser("list", help="what you watch")
    remove = actions.add_parser("remove", help="stop watching")
    remove.add_argument("id", type=int)
    watch.set_defaults(run=cmd_watch)

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
