from datetime import datetime

from uatu.web.schemas import OutputSchema


class LatestPass(OutputSchema):
    id: str  # see passes
    acquired_at: datetime
    platform: str
    zone: str


class Status(OutputSchema):
    recording: bool  # connected to aisstream.io right now
    last_ais_at: datetime | None
    vessels_last_hour: int
    events_24h: int  # notable or alerts, going on during the last 24 h
    alerts_24h: int
    latest_pass: LatestPass | None
