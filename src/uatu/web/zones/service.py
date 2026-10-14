"""The zones Uatu analyses."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import Zone
from uatu.db.models import RecordedZone
from uatu.web.basemap.service import area
from uatu.web.scope import WHOLE_COAST
from uatu.web.zones.schemas import ZoneInfo, Zones


async def zones(db: AsyncSession, analysed: list[Zone]) -> Zones:
    since = {key: at for key, at in await db.execute(select(RecordedZone.key, RecordedZone.since))}
    return Zones(
        zones=[
            ZoneInfo(key=z.key, name=z.name, recording_since=since.get(z.key), **area(z.bbox).model_dump())
            for z in analysed
        ],
        whole_coast=area(WHOLE_COAST),
    )
