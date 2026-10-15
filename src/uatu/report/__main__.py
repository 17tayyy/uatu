"""A report of the last whole days: Markdown, figures and one HTML file, in Spanish.

uatu-report [--days 7] [--until YYYY-MM-DD] [--zone KEY] [--out REPORTS_DIR]
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from uatu import db
from uatu.config import ZONES, ZoneSettings
from uatu.log import configure_logging
from uatu.report.data import MADRID
from uatu.report.write import write_report
from uatu.sar.config import SarSettings


class ReportSettings(SarSettings, ZoneSettings):
    reports_dir: Path = Path("data/reports")


async def build(settings: ReportSettings, args: argparse.Namespace) -> Path:
    until = date.fromisoformat(args.until) if args.until else datetime.now(MADRID).date()
    zones = [ZONES[args.zone]] if args.zone else settings.zones
    engine = db.create_engine(settings.database_url)
    try:
        folder, _ = await write_report(
            engine, until, args.days, zones, ZONES, settings.sar_data_dir, Path(args.out or settings.reports_dir)
        )
    finally:
        await engine.dispose()
    return folder / "informe.md"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uatu-report", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--days", type=int, default=7, help="whole days (default: 7)")
    parser.add_argument("--until", help="the day after the last one, YYYY-MM-DD (default: today)")
    parser.add_argument("--zone", choices=sorted(ZONES), help="one zone (default: the analysed ones)")
    parser.add_argument("--out", help="folder for the reports (default: REPORTS_DIR, data/reports)")
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
