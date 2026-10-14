"""Who owns and manages vessels, from OpenSanctions: a vessel's companies, a company's fleet."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import (
    Position,
    SanctionedVessel,
    ShipCompany,
    ShipLink,
    Vessel,
    VesselRisk,
    WatchlistedVessel,
)
from uatu.web.companies.schemas import CompanyDetail, CompanyRole, FleetVessel
from uatu.web.errors import NotFoundError

ROLE_ORDER = ["owner", "commercial_manager", "ism_manager", "linked", "insurer"]


def _roles(links: Iterable[ShipLink]) -> list[str]:
    return sorted({link.role for link in links}, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)


async def vessel_companies(db: AsyncSession, imo: int) -> list[CompanyRole]:
    """The companies behind a vessel: sanctioned first, then owners, managers, insurers."""
    rows = await db.execute(
        select(ShipLink, ShipCompany)
        .join(ShipCompany, ShipCompany.id == ShipLink.company_id)
        .where(ShipLink.vessel_imo == imo)
        .order_by(ShipLink.id)
    )
    by_company: dict[str, tuple[ShipCompany, list[ShipLink]]] = {}
    for link, company in rows:
        by_company.setdefault(company.id, (company, []))[1].append(link)
    result = [
        CompanyRole(
            id=c.id,
            name=c.name,
            kind=c.kind,
            sanctioned=c.sanctioned,
            programs=c.programs,
            vessels=c.vessels,
            sanctioned_vessels=c.sanctioned_vessels,
            roles=_roles(links),
            sources=sorted({link.source for link in links}),
            since=min((link.since for link in links if link.since), default=None),
            as_of=max((link.as_of for link in links if link.as_of), default=None),
        )
        for c, links in by_company.values()
    ]
    result.sort(key=lambda c: (not c.sanctioned, ROLE_ORDER.index(c.roles[0]), c.name))
    return result


async def get_company(db: AsyncSession, company_id: str) -> CompanyDetail:
    company = await db.get(ShipCompany, company_id)
    if company is None:
        raise NotFoundError("Company")
    links = (await db.scalars(select(ShipLink).where(ShipLink.company_id == company_id).order_by(ShipLink.id))).all()
    by_imo: dict[int, list[ShipLink]] = defaultdict(list)
    for link in links:
        by_imo[link.vessel_imo].append(link)
    imos = list(by_imo)

    sanctioned = set(await db.scalars(select(SanctionedVessel.imo).where(SanctionedVessel.imo.in_(imos))))
    risks = dict((await db.execute(select(VesselRisk.imo, VesselRisk.level).where(VesselRisk.imo.in_(imos)))).all())
    watchlists: dict[int, list[str]] = defaultdict(list)
    for imo, watchlist in await db.execute(
        select(WatchlistedVessel.imo, WatchlistedVessel.watchlist).where(WatchlistedVessel.imo.in_(imos))
    ):
        watchlists[imo].append(watchlist)
    last_seen = (
        select(func.max(Position.received_at)).where(Position.mmsi == Vessel.mmsi).correlate(Vessel).scalar_subquery()
    )
    heard: dict[int, tuple[int, str | None, datetime | None]] = {}  # imo -> the MMSI it was last heard with
    for mmsi, imo, name, seen in await db.execute(
        select(Vessel.mmsi, Vessel.imo, Vessel.name, last_seen)
        .where(Vessel.imo.in_(imos))
        .order_by(last_seen.desc().nulls_last())
    ):
        heard.setdefault(imo, (mmsi, name, seen))

    fleet = [
        FleetVessel(
            imo=imo,
            name=vessel_links[0].vessel_name,
            roles=_roles(vessel_links),
            sources=sorted({link.source for link in vessel_links}),
            sanctioned=imo in sanctioned,
            risk=risks.get(imo),
            watchlists=sorted(watchlists[imo]),
            mmsi=heard[imo][0] if imo in heard else None,
            ais_name=heard[imo][1] if imo in heard else None,
            last_seen_at=heard[imo][2] if imo in heard else None,
        )
        for imo, vessel_links in by_imo.items()
    ]
    fleet.sort(
        key=lambda v: (v.mmsi is None, -v.last_seen_at.timestamp() if v.last_seen_at else 0, not v.sanctioned, v.name)
    )
    return CompanyDetail(
        id=company.id,
        name=company.name,
        kind=company.kind,
        countries=company.countries.split(";") if company.countries else [],
        imos=company.imos.split(";") if company.imos else [],
        sanctioned=company.sanctioned,
        programs=company.programs,
        vessels=company.vessels,
        sanctioned_vessels=company.sanctioned_vessels,
        fleet=fleet,
    )
