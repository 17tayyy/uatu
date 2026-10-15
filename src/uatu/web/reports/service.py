"""The reports the worker writes (uatu-report): the list, and their files."""

import re
from datetime import date
from pathlib import Path

from uatu.web.errors import NotFoundError
from uatu.web.reports.schemas import ReportSummary

NAME = re.compile(r"^informe-(\d{4}-\d{2}-\d{2})$")
FILE = re.compile(r"^[a-z0-9-]+\.(html|md|png)$")
MEDIA = {"html": "text/html; charset=utf-8", "md": "text/markdown; charset=utf-8", "png": "image/png"}


def list_reports(reports_dir: Path) -> list[ReportSummary]:
    """Newest first."""
    found = []
    for folder in reports_dir.glob("informe-*") if reports_dir.is_dir() else []:
        match = NAME.match(folder.name)
        if match and (folder / "informe.html").exists():
            found.append(
                ReportSummary(
                    name=folder.name,
                    last_day=date.fromisoformat(match.group(1)),
                    url=f"/api/v1/reports/{folder.name}/informe.html",
                )
            )
    return sorted(found, key=lambda r: r.last_day, reverse=True)


def report_file(reports_dir: Path, name: str, file: str) -> tuple[Path, str]:
    """(path, media type) of one file of a report; names are checked, so nothing outside can be asked for."""
    if not NAME.match(name) or not FILE.match(file):
        raise NotFoundError("Report")
    path = reports_dir / name / file
    if not path.is_file():
        raise NotFoundError("Report")
    return path, MEDIA[file.rsplit(".", 1)[1]]
