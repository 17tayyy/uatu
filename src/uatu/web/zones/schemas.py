from datetime import datetime

from uatu.web.basemap.schemas import MapArea
from uatu.web.schemas import OutputSchema


class ZoneInfo(MapArea):
    key: str
    name: str
    recording_since: datetime | None  # since when AIS is recorded there


class Zones(OutputSchema):
    zones: list[ZoneInfo]  # south-west to north-east
    whole_coast: MapArea  # every zone recorded, analysed or not yet
