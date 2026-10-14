"""Is Uatu listening, and what has it seen lately: the header of the dashboard."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.config import BBox
from uatu.db.models import AisEvent, IngestSession, Position, SarScene
from uatu.web.geo import inside
from uatu.web.passes.service import pass_id
from uatu.web.status.schemas import LatestPass, Status


async def status(db: AsyncSession, bbox: BBox, zones: list[str]) -> Status:
    """Recording is global; what was seen is counted within the zones."""
    now = datetime.now(UTC)
    session = (
        await db.execute(
            select(IngestSession.last_message_at, IngestSession.disconnected_at)
            .order_by(IngestSession.connected_at.desc(), IngestSession.id.desc())
            .limit(1)
        )
    ).first()
    last_ais_at = await db.scalar(select(func.max(IngestSession.last_message_at)))
    vessels = await db.scalar(
        select(func.count(distinct(Position.mmsi))).where(
            Position.received_at >= now - timedelta(hours=1), inside(Position.lat, Position.lon, bbox)
        )
    )
    day_ago = now - timedelta(hours=24)
    events = await db.execute(
        select(AisEvent.severity, func.count())
        .where(
            AisEvent.severity >= 2,
            inside(AisEvent.lat, AisEvent.lon, bbox),
            or_(AisEvent.start_at >= day_ago, AisEvent.end_at.is_(None), AisEvent.end_at >= day_ago),
        )
        .group_by(AisEvent.severity)
    )
    severities: dict[int, int] = {severity: n for severity, n in events}
    started = func.min(SarScene.acquired_from)
    latest = (
        await db.execute(
            select(SarScene.platform, SarScene.datatake, SarScene.zone, started.label("acquired_at"))
            .where(SarScene.status == "processed", SarScene.zone.in_(zones))
            .group_by(SarScene.platform, SarScene.datatake, SarScene.zone)
            .order_by(started.desc(), SarScene.platform, SarScene.datatake, SarScene.zone)
            .limit(1)
        )
    ).first()
    return Status(
        recording=session is not None and session.disconnected_at is None,
        last_ais_at=last_ais_at,
        vessels_last_hour=vessels or 0,
        events_24h=sum(severities.values()),
        alerts_24h=severities.get(3, 0),
        latest_pass=LatestPass(
            id=pass_id(latest.platform, latest.datatake, latest.zone),
            acquired_at=latest.acquired_at,
            platform=latest.platform,
            zone=latest.zone,
        )
        if latest
        else None,
    )
