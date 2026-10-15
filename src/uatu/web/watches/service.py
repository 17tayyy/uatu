"""Your own watch list: vessels the AIS analysis alerts on whenever they are heard (as uatu-ais watch)."""

from collections import defaultdict

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import Position, Vessel, WatchedVessel
from uatu.web.errors import NotFoundError
from uatu.web.watches.schemas import HeardVessel, WatchCreate, WatchRead

MAX_VESSELS = 10  # a name can be common: a few of them are enough to tell


async def list_watches(db: AsyncSession, ids: list[int] | None = None) -> list[WatchRead]:
    query = select(WatchedVessel).order_by(WatchedVessel.added_at.desc(), WatchedVessel.id.desc())
    if ids is not None:
        query = query.where(WatchedVessel.id.in_(ids))
    watches = (await db.scalars(query)).all()
    if not watches:
        return []
    last_seen = (
        select(func.max(Position.received_at)).where(Position.mmsi == Vessel.mmsi).correlate(Vessel).scalar_subquery()
    )
    heard = await db.execute(
        select(WatchedVessel.id, Vessel.mmsi, Vessel.name, last_seen.label("last_seen_at"))
        .join(
            Vessel,
            or_(
                Vessel.imo == WatchedVessel.imo,
                Vessel.mmsi == WatchedVessel.mmsi,
                func.upper(Vessel.name) == func.upper(WatchedVessel.name),
            ),
        )
        .where(WatchedVessel.id.in_([w.id for w in watches]))
        .order_by(WatchedVessel.id, last_seen.desc().nulls_last(), Vessel.mmsi)
    )
    vessels: dict[int, list[HeardVessel]] = defaultdict(list)
    for r in heard:
        if len(vessels[r.id]) < MAX_VESSELS:
            vessels[r.id].append(HeardVessel(mmsi=r.mmsi, name=r.name, last_seen_at=r.last_seen_at))
    return [
        WatchRead(id=w.id, imo=w.imo, mmsi=w.mmsi, name=w.name, note=w.note, added_at=w.added_at, vessels=vessels[w.id])
        for w in watches
    ]


async def add_watch(db: AsyncSession, data: WatchCreate) -> WatchRead:
    watch = WatchedVessel(**data.model_dump())
    db.add(watch)
    await db.commit()
    [read] = await list_watches(db, [watch.id])
    return read


async def remove_watch(db: AsyncSession, watch_id: int) -> None:
    removed = await db.execute(delete(WatchedVessel).where(WatchedVessel.id == watch_id))
    if removed.rowcount == 0:
        raise NotFoundError("Watch")
    await db.commit()
