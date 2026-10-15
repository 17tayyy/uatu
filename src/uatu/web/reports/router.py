from fastapi import APIRouter
from fastapi.responses import FileResponse

from uatu.web.dependencies import Settings
from uatu.web.reports import service as report_service
from uatu.web.reports.schemas import ReportSummary
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def list_reports(settings: Settings) -> DataResponse[list[ReportSummary]]:
    """The reports written so far (weekly, or by uatu-report), newest first."""
    return DataResponse(data=report_service.list_reports(settings.reports_dir))


@router.get("/{name}/{file}", response_class=FileResponse)
async def report_file(name: str, file: str, settings: Settings) -> FileResponse:
    path, media_type = report_service.report_file(settings.reports_dir, name, file)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})
