from datetime import datetime
from typing import Any

from uatu.web.schemas import OutputSchema


class EventProperties(OutputSchema):
    id: int
    kind: str  # ais_gap | encounter | loitering | infrastructure_loitering | sanctioned_vessel | ...
    severity: int  # 1 informative, 2 notable, 3 alert
    mmsi: int
    vessel_name: str | None
    other_mmsi: int | None
    start_at: datetime
    end_at: datetime | None
    title: str
    message: str
    details: dict[str, Any]
