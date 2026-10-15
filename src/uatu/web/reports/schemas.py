from datetime import date

from uatu.web.schemas import OutputSchema


class ReportSummary(OutputSchema):
    name: str  # informe-<last day>
    last_day: date
    url: str  # the self-contained HTML
