"""Is Uatu listening, and what has it seen lately: the header of the dashboard."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import AisEvent, IngestSession, Position, SarScene
from uatu.web.status.schemas import LatestPass, Status


async def status(db: AsyncSession) -> Status:
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
        select(func.count(distinct(Position.mmsi))).where(Position.received_at >= now - timedelta(hours=1))
    )
    day_ago = now - timedelta(hours=24)
    events = await db.execute(
        select(AisEvent.severity, func.count())
        .where(
            AisEvent.severity >= 2,
            or_(AisEvent.start_at >= day_ago, AisEvent.end_at.is_(None), AisEvent.end_at >= day_ago),
        )
        .group_by(AisEvent.severity)
    )
    severities: dict[int, int] = {severity: n for severity, n in events}
    latest = (
        await db.execute(
            select(SarScene.id, SarScene.acquired_from, SarScene.platform)
            .where(SarScene.status == "processed")
            .order_by(SarScene.acquired_from.desc(), SarScene.id.desc())
            .limit(1)
        )
    ).first()
    return Status(
        recording=session is not None and session.disconnected_at is None,
        last_ais_at=last_ais_at,
        vessels_last_hour=vessels or 0,
        events_24h=sum(severities.values()),
        alerts_24h=severities.get(3, 0),
        latest_pass=LatestPass(id=latest.id, acquired_at=latest.acquired_from, platform=latest.platform)
        if latest
        else None,
    )
