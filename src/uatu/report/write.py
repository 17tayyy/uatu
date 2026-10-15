"""A report written to disk: what the command and the weekly task both do."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.config import Zone
from uatu.report import figures
from uatu.report.data import MADRID, Report, gather
from uatu.report.html import to_html
from uatu.report.render import day, number, plural, render


def announcement(folder: Path, r: Report, dashboard_url: str | None) -> tuple[str, str, str | None]:
    """(title, message, link) of the notification that a report is ready."""
    title = f"Uatu: informe del {day(r.start)} al {day(r.end - timedelta(seconds=1))}"
    message = (
        f"{number(r.vessels_heard)} barcos oídos, {plural(len(r.passes), 'pasada', 'pasadas')} de radar,"
        f" {plural(len(r.candidates), 'candidato', 'candidatos')} sin AIS,"
        f" {plural(len(r.listed), 'barco sancionado o vigilado', 'barcos sancionados o vigilados')}."
    )
    link = f"{dashboard_url.rstrip('/')}/api/v1/reports/{folder.name}/informe.html" if dashboard_url else None
    return title, message, link


def folder_name(last_day: date) -> str:
    return f"informe-{last_day:%Y-%m-%d}"


async def write_report(
    engine: AsyncEngine, until: date, days: int, zones: list[Zone], all_zones: dict[str, Zone], data_dir: Path,
    out: Path,
) -> tuple[Path, Report]:  # fmt: skip
    """The `days` whole days (Spanish time) before `until`, as out/informe-<last day>/: informe.md, informe.html
    and figures. The folder, and what it says."""
    end = datetime.combine(until, time(), MADRID).astimezone(UTC)
    report = await gather(engine, end - timedelta(days=days), end, zones, all_zones, data_dir)
    folder = out / folder_name(until - timedelta(days=1))
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    if report.listed:
        elsewhere = figures.listed_map(report, folder / "barcos-vigilados.png")
        made["listed"] = "barcos-vigilados.png"
        if elsewhere:
            made["listed_elsewhere"] = ", ".join(elsewhere)
    if report.transits_by_day:
        figures.transits_chart(report, folder / "cruces-del-estrecho.png")
        made["transits"] = "cruces-del-estrecho.png"
    if report.density:
        figures.density_map(report, folder / "densidad.png")
        made["density"] = "densidad.png"
    md = folder / "informe.md"
    md.write_text(render(report, made))
    to_html(md)
    return folder, report
