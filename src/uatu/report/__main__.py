"""A report of the last whole days: Markdown and figures, in Spanish.

uatu-report [--days 7] [--until YYYY-MM-DD] [--zone KEY] [--out data/reports]
"""

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from pydantic import ValidationError

from uatu import db
from uatu.config import ZONES, ZoneSettings
from uatu.log import configure_logging
from uatu.report import figures
from uatu.report.data import MADRID, gather
from uatu.report.html import to_html
from uatu.report.render import render
from uatu.sar.config import SarSettings


class ReportSettings(SarSettings, ZoneSettings):
    pass


async def build(settings: ReportSettings, args: argparse.Namespace) -> Path:
    # Whole days, in Spanish time: up to the midnight that begins --until (default: today).
    until = date.fromisoformat(args.until) if args.until else datetime.now(MADRID).date()
    end = datetime.combine(until, time(), MADRID).astimezone(UTC)
    start = end - timedelta(days=args.days)
    zones = [ZONES[args.zone]] if args.zone else settings.zones
    engine = db.create_engine(settings.database_url)
    try:
        report = await gather(engine, start, end, zones, ZONES, settings.sar_data_dir)
    finally:
        await engine.dispose()

    folder = Path(args.out) / f"informe-{until - timedelta(days=1):%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    if report.listed:
        made["listed_elsewhere"] = ", ".join(figures.listed_map(report, folder / "barcos-vigilados.png"))
        made["listed"] = "barcos-vigilados.png"
    if report.transits_by_day:
        figures.transits_chart(report, folder / "cruces-del-estrecho.png")
        made["transits"] = "cruces-del-estrecho.png"
    if report.density:
        figures.density_map(report, folder / "densidad.png")
        made["density"] = "densidad.png"
    path = folder / "informe.md"
    path.write_text(render(report, {k: v for k, v in made.items() if v}))
    to_html(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uatu-report", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--days", type=int, default=7, help="whole days (default: 7)")
    parser.add_argument("--until", help="the day after the last one, YYYY-MM-DD (default: today)")
    parser.add_argument("--zone", choices=sorted(ZONES), help="one zone (default: the analysed ones)")
    parser.add_argument("--out", default="data/reports", help="folder for the report (default: data/reports)")
    args = parser.parse_args()
    try:
        settings = ReportSettings()
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        sys.exit(2)
    configure_logging("WARNING", "console")
    print(asyncio.run(build(settings, args)))


if __name__ == "__main__":
    main()
