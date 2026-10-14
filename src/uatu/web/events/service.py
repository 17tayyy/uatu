"""What the AIS detectors found: gaps, encounters, loitering, vessels over cables, sanctioned vessels..."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.ais.analysis import describe
from uatu.ais.events import Event
from uatu.db.models import AisEvent, Vessel
from uatu.web.events.schemas import EventProperties
from uatu.web.schemas import Feature, FeatureCollection, point


async def list_events(
    db: AsyncSession, hours: float, min_severity: int, limit: int
) -> FeatureCollection[EventProperties]:
    """Events going on during the last `hours`, latest first."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(AisEvent)
            .where(
                AisEvent.severity >= min_severity,
                or_(AisEvent.start_at >= since, AisEvent.end_at.is_(None), AisEvent.end_at >= since),
            )
            .order_by(AisEvent.start_at.desc(), AisEvent.id.desc())
            .limit(limit)
        )
    ).scalars()
    events = list(rows)
    mmsis = {e.mmsi for e in events} | {e.other_mmsi for e in events if e.other_mmsi}
    names: dict[int, str] = {}
    if mmsis:
        found = await db.execute(select(Vessel.mmsi, Vessel.name).where(Vessel.mmsi.in_(mmsis)))
        names = {mmsi: name for mmsi, name in found if name}

    features = []
    for e in events:
        title, message, _ = describe(
            Event(e.kind, e.severity, e.mmsi, e.start_at, e.lat, e.lon, e.details, e.end_at, e.other_mmsi), names
        )
        features.append(
            Feature(
                geometry=point(e.lon, e.lat),
                properties=EventProperties(
                    id=e.id,
                    kind=e.kind,
                    severity=e.severity,
                    mmsi=e.mmsi,
                    vessel_name=names.get(e.mmsi),
                    other_mmsi=e.other_mmsi,
                    start_at=e.start_at,
                    end_at=e.end_at,
                    title=title,
                    message=message,
                    details=e.details,
                ),
            )
        )
    return FeatureCollection(features=features)
