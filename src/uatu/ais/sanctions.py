"""Sanctions, and the companies behind vessels, from OpenSanctions (CC BY-NC 4.0).

An IMO number stays with a hull through renames and reflagging, so it is the key throughout.
Sanctions lists name vessels and the companies they belong to. Other sources say who owns and
manages vessels, sanctioned or not:

- Ukraine's military intelligence (GUR, "War & Sanctions"): the Russian shadow fleet and other
  vessels serving the war, with their owners and their commercial and ISM managers.
- The Paris MoU's list of ships banned from European ports, with their ISM company.
- Port state control detentions in the Tokyo and Black Sea MoUs: the ISM company at the time.

OpenSanctions resolves one company across sources to one entity id, so the manager GUR names is
recognisably the company a sanctions list names. A vessel off the lists is worth a look when a
company running it is sanctioned, or runs a fleet that mostly is.
"""

import asyncio
import json
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.db.models import SanctionedVessel, ShipCompany, ShipLink, VesselRisk, WatchlistedVessel

log = structlog.get_logger(component="reference")

ENTITIES = "https://data.opensanctions.org/datasets/latest/{}/entities.ftm.json"
SANCTIONS = "sanctions"  # the collection of every sanctions list: EU, UK, US, CH, CA, AU...
GUR = "ua_war_sanctions"
PORT_BANS = "paris_mou_banned"
DETENTIONS = ("tokyo_mou_detention", "black_sea_mou_detention")
DATASETS = (SANCTIONS, GUR, PORT_BANS, *DETENTIONS)
# An ISM company recorded at an older detention has often changed since.
DETENTION_MAX_AGE = timedelta(days=3 * 365)

ROLES = {  # the role as the sources word it, lower-cased -> ours; anything else is "linked"
    "owner": "owner",
    "registered owner": "owner",
    "owned or controlled by": "owner",
    "property in the interest of": "owner",
    "owns, controls, or operates": "owner",
    "commerce manager": "commercial_manager",
    "security manager": "ism_manager",  # GUR's name for the ISM (safety management) company
    "ism company": "ism_manager",  # Paris MoU
    "company": "ism_manager",  # Tokyo MoU detentions
    "associated company": "ism_manager",  # Black Sea MoU detentions
    "p&i club": "insurer",
}
SURVEYORS = {"classification society", "related recognised organization"}  # inspect ships, don't run them
RUNS = {"owner", "commercial_manager", "ism_manager"}  # the roles that make up a company's fleet
# A company's sanctions reflect on the vessels it answers for. An insurer covers hundreds of
# fleets: its sanctions count, but less.
ANSWERABLE = RUNS | {"linked"}

PARTIES = {"Company", "Organization", "LegalEntity", "PublicBody", "Person"}
LINKS = {"Ownership", "UnknownLink"}
SCHEMA = re.compile(r'"schema":"(\w+)"')

ROLE_NAMES = {
    "owner": "propietario",
    "commercial_manager": "gestor comercial",
    "ism_manager": "gestor ISM",
    "insurer": "aseguradora",
    "linked": "empresa vinculada",
}
REASON_ORDER = ["sanctioned_company", "sanctioned_fleet", "shadow_fleet", "war_and_sanctions", "port_ban"]


@dataclass(frozen=True)
class Tables:
    sanctioned: list[dict[str, Any]]
    companies: list[dict[str, Any]]
    links: list[dict[str, Any]]
    watchlists: list[dict[str, Any]]
    risks: list[dict[str, Any]]


@dataclass
class Graph:
    """Vessels, the parties behind them and the links between, merged across datasets by entity id."""

    today: date
    vessels: dict[str, dict[str, Any]] = field(default_factory=dict)
    parties: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: list[tuple[str, str, str, date | None, date | None, str]] = field(default_factory=list)
    measures: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))

    def add(self, entities: Iterable[dict[str, Any]], source: str) -> None:
        """Entities of one dataset. Sanctions (measures) only count for vessels added before them."""
        for e in entities:
            schema, p = e["schema"], e["properties"]
            if schema == "Vessel":
                self._vessel(e, p, source)
            elif schema in PARTIES:
                self._party(e, p, schema, source)
            elif schema in LINKS:
                ends = [*p.get("owner", []), *p.get("subject", [])], [*p.get("asset", []), *p.get("object", [])]
                label = "; ".join(p.get("role", [])).strip().lower()
                for a in ends[0]:
                    for b in ends[1]:
                        self.links.append((a, b, label, _date(p.get("startDate")), _date(p.get("date")), source))
            elif schema == "Sanction":
                for target in p.get("entity", []):
                    if target in self.vessels:
                        self.measures[target, source].append(_measure(p))

    def _vessel(self, e: dict[str, Any], p: dict[str, list[str]], source: str) -> None:
        v = self.vessels.setdefault(
            e["id"],
            {"id": e["id"], "names": [], "imos": set(), "mmsis": set(), "countries": [], "programs": set(),
             "sanctioned": False, "lists": {}},
        )  # fmt: skip
        _extend(v["names"], [e["caption"], *p.get("name", []), *p.get("alias", []), *p.get("previousName", [])])
        v["imos"] |= {int(i[3:]) for i in p.get("imoNumber", []) if i.startswith("IMO") and i[3:].isdigit()}
        v["mmsis"] |= {m for m in p.get("mmsi", []) if len(m) == 9 and m.isdigit()}
        _extend(v["countries"], [*p.get("flag", []), *p.get("country", [])])
        topics = set(p.get("topics", []))
        if source == SANCTIONS and "sanction" in topics:  # not "sanction.linked" nor "sanction.counter"
            v["sanctioned"] = True
            v["programs"] |= set(p.get("programId", []))
        v["lists"][source] = {"topics": topics, "description": next(iter(p.get("description", [])), None)}

    def _party(self, e: dict[str, Any], p: dict[str, list[str]], schema: str, source: str) -> None:
        c = self.parties.setdefault(
            e["id"],
            {"id": e["id"], "name": e["caption"], "kind": "person" if schema == "Person" else "company",
             "countries": [], "imos": [], "programs": set(), "sanctioned": False},
        )  # fmt: skip
        _extend(c["countries"], [*p.get("country", []), *p.get("jurisdiction", [])])
        _extend(c["imos"], [i[3:] for i in p.get("imoNumber", []) if i.startswith("IMO") and i[3:].isdigit()])
        if source == SANCTIONS and "sanction" in p.get("topics", []):
            c["sanctioned"] = True
            c["programs"] |= set(p.get("programId", []))

    def tables(self) -> Tables:
        sanctioned_imos = {imo for v in self.vessels.values() if v["sanctioned"] for imo in v["imos"]}
        links = self._links()
        fleets: dict[str, set[int]] = defaultdict(set)
        for link in links:
            if link["role"] in RUNS:
                fleets[link["company_id"]].add(link["vessel_imo"])
        companies = {}
        for company_id in dict.fromkeys(link["company_id"] for link in links):
            c = self.parties[company_id]
            companies[company_id] = {
                "id": company_id,
                "name": c["name"],
                "kind": c["kind"],
                "countries": ";".join(c["countries"]) or None,
                "imos": ";".join(c["imos"]) or None,
                "sanctioned": c["sanctioned"],
                "programs": ";".join(sorted(c["programs"])) or None,
                "vessels": len(fleets[company_id]),
                "sanctioned_vessels": len(fleets[company_id] & sanctioned_imos),
            }
        watchlists = self._watchlists()

        by_vessel: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for link in links:
            by_vessel[link["vessel_imo"]].append(link)
        listed: dict[int, list[str]] = defaultdict(list)
        for w in watchlists:
            listed[w["imo"]].append(w["watchlist"])
        risks = []
        for imo in sorted((by_vessel.keys() | listed.keys()) - sanctioned_imos):
            found = assess(by_vessel.get(imo, []), companies, listed.get(imo, []))
            if found:
                risks.append({"imo": imo, "level": found[0], "reasons": found[1]})
        return Tables(self._sanctioned(), list(companies.values()), links, watchlists, risks)

    def _sanctioned(self) -> list[dict[str, Any]]:
        by_imo: dict[int, dict[str, Any]] = {}
        for v in self.vessels.values():
            if not v["sanctioned"]:
                continue
            name, *aliases = v["names"]
            for imo in sorted(v["imos"]):
                by_imo.setdefault(
                    imo,
                    {
                        "imo": imo,
                        "name": name,
                        "aliases": ";".join(aliases) or None,
                        "mmsis": ";".join(sorted(v["mmsis"])) or None,
                        "countries": ";".join(v["countries"]) or None,
                        "programs": ";".join(sorted(v["programs"])) or None,
                        "sanctions": "; ".join(self.measures.get((v["id"], SANCTIONS), []))[:2000] or None,
                        "source_id": v["id"],
                    },
                )
        return list(by_imo.values())

    def _links(self) -> list[dict[str, Any]]:
        """One row per vessel, company, role and source; surveyors and old detentions left out."""
        oldest_detention = self.today - DETENTION_MAX_AGE
        rows: dict[tuple[int, str, str, str], dict[str, Any]] = {}
        for a, b, label, since, as_of, source in self.links:
            if a in self.vessels and b in self.parties:
                vessel, company_id = self.vessels[a], b
            elif b in self.vessels and a in self.parties:
                vessel, company_id = self.vessels[b], a
            else:
                continue  # between companies, or people
            if label in SURVEYORS or (source in DETENTIONS and (as_of is None or as_of < oldest_detention)):
                continue
            role = ROLES.get(label, "linked")
            for imo in vessel["imos"]:
                row = rows.setdefault(
                    (imo, company_id, role, source),
                    {"vessel_imo": imo, "vessel_name": vessel["names"][0], "company_id": company_id, "role": role,
                     "role_label": label or None, "since": since, "as_of": as_of, "source": source},
                )  # fmt: skip
                row["since"] = min(filter(None, (row["since"], since)), default=None)
                row["as_of"] = max(filter(None, (row["as_of"], as_of)), default=None)
        return list(rows.values())

    def _watchlists(self) -> list[dict[str, Any]]:
        rows: dict[tuple[int, str], dict[str, Any]] = {}
        for v in self.vessels.values():
            found = []
            if gur := v["lists"].get(GUR):
                shadow = "mare.shadow" in gur["topics"]
                found.append(("shadow_fleet" if shadow else "war_and_sanctions", gur["description"]))
            if PORT_BANS in v["lists"]:
                found.append(("port_ban", "; ".join(self.measures.get((v["id"], PORT_BANS), [])) or None))
            for watchlist, note in found:
                for imo in v["imos"]:
                    rows.setdefault(
                        (imo, watchlist),
                        {"imo": imo, "watchlist": watchlist, "name": v["names"][0], "note": note and note[:2000],
                         "source_id": v["id"]},
                    )  # fmt: skip
        return list(rows.values())


def assess(
    links: list[dict[str, Any]], companies: dict[str, dict[str, Any]], watchlists: Iterable[str]
) -> tuple[str, list[dict[str, Any]]] | None:
    """Why a vessel off the sanctions lists is worth a look, if it is: (level, reasons).

    sanctioned_owner: a company that owns, manages or is linked to it is sanctioned. Otherwise
    watchlisted: one that owns or manages it has at least 2 sanctioned vessels, and they are at
    least half of its known fleet (a big manager that once ran a vessel sanctioned later is not
    that); or its insurer is sanctioned; or it is on GUR's lists or banned from European ports.
    """
    reasons: dict[tuple[str, str, str], dict[str, Any]] = {}
    for link in links:
        c, role = companies[link["company_id"]], link["role"]
        about = {"company_id": c["id"], "company": c["name"], "role": role}
        if c["sanctioned"] and role in ANSWERABLE:
            reason = {"kind": "sanctioned_company", **about, "programs": c["programs"]}
        elif c["sanctioned"] and role == "insurer":
            reason = {"kind": "sanctioned_insurer", **about, "programs": c["programs"]}
        elif role in RUNS and c["sanctioned_vessels"] >= 2 and 2 * c["sanctioned_vessels"] >= c["vessels"]:
            reason = {"kind": "sanctioned_fleet", **about, "sanctioned_vessels": c["sanctioned_vessels"],
                      "vessels": c["vessels"]}  # fmt: skip
        else:
            continue
        reasons.setdefault((reason["kind"], c["id"], role), reason)
    found = [*reasons.values(), *({"kind": w} for w in dict.fromkeys(watchlists))]
    if not found:
        return None
    found.sort(key=lambda r: REASON_ORDER.index(r["kind"]) if r["kind"] in REASON_ORDER else len(REASON_ORDER))
    level = "sanctioned_owner" if found[0]["kind"] == "sanctioned_company" else "watchlisted"
    return level, found


def explain(reason: dict[str, Any]) -> str:
    """One reason of `assess`, in Spanish."""
    programs = f" ({reason['programs'].replace(';', ', ')})" if reason.get("programs") else ""
    role = ROLE_NAMES.get(reason.get("role", ""), "")
    match reason["kind"]:
        case "sanctioned_company" if reason["role"] == "linked":
            return f"está vinculado a {reason['company']}, en listas de sanciones{programs}"
        case "sanctioned_company":
            return f"su {role}, {reason['company']}, está en listas de sanciones{programs}"
        case "sanctioned_insurer":
            return f"su aseguradora, {reason['company']}, está en listas de sanciones{programs}"
        case "sanctioned_fleet":
            return (
                f"su {role}, {reason['company']}, tiene sancionados {reason['sanctioned_vessels']}"
                f" de sus {reason['vessels']} barcos conocidos"
            )
        case "shadow_fleet":
            return "la inteligencia ucraniana (GUR) lo cuenta en la flota en la sombra rusa"
        case "war_and_sanctions":
            return "figura en la lista de la inteligencia ucraniana (GUR) de barcos que trabajan para Rusia"
        case "port_ban":
            return "tiene prohibida la entrada en los puertos europeos (Paris MoU)"
        case kind:
            return kind


async def refresh_sanctions(engine: AsyncEngine, http: httpx.AsyncClient) -> Tables:
    """Replace sanctioned vessels, the companies behind vessels and the watchlists with the latest data."""
    graph = Graph(today=datetime.now(UTC).date())
    with tempfile.TemporaryDirectory() as tmp:
        for dataset in DATASETS:  # the sanctions collection first: it says which companies are sanctioned
            path = Path(tmp) / f"{dataset}.json"
            async with http.stream("GET", ENTITIES.format(dataset), follow_redirects=True) as response:
                response.raise_for_status()
                with path.open("wb") as f:  # up to ~400 MB: streamed to disk rather than held in memory
                    async for chunk in response.aiter_bytes(2**20):
                        f.write(chunk)
            await asyncio.to_thread(_read, path, dataset, graph)
            path.unlink()
    tables = await asyncio.to_thread(graph.tables)
    async with engine.begin() as conn:
        for model in (VesselRisk, WatchlistedVessel, ShipLink, ShipCompany, SanctionedVessel):
            await conn.execute(delete(model))
        for model, rows in [
            (SanctionedVessel, tables.sanctioned),
            (ShipCompany, tables.companies),
            (ShipLink, tables.links),
            (WatchlistedVessel, tables.watchlists),
            (VesselRisk, tables.risks),
        ]:
            if rows:
                await conn.execute(insert(model), rows)
    log.info(
        "sanctions_refreshed",
        vessels=len(tables.sanctioned),
        companies=len(tables.companies),
        links=len(tables.links),
        watchlisted=len(tables.watchlists),
        worth_a_look=len(tables.risks),
    )
    return tables


def _read(path: Path, dataset: str, graph: Graph) -> None:
    graph.add(_entities(path, {"Vessel", *PARTIES, *LINKS}), dataset)
    graph.add(_entities(path, {"Sanction"}), dataset)  # once its vessels are known: there are 170,000


def _entities(path: Path, schemas: set[str]) -> Iterator[dict[str, Any]]:
    """The entities of the given schemas in a FollowTheMoney export, one JSON object per line."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            if (m := SCHEMA.search(line)) and m[1] in schemas:  # cheaper than parsing every line
                yield json.loads(line)


def _measure(p: dict[str, list[str]]) -> str:
    """A sanction or ban, in the source's words: 'date · authority · reason · duration'."""
    when = next(iter(p.get("startDate", []) or p.get("listingDate", [])), None)
    why = next(iter(p.get("reason", []) or p.get("program", [])), None)
    parts = [when, ", ".join(p.get("authority", [])), why, next(iter(p.get("duration", [])), None)]
    return " · ".join(part for part in parts if part)


def _date(values: list[str] | None) -> date | None:
    """The first of FollowTheMoney's dates, which may be just a year or a month."""
    if not values:
        return None
    text = values[0][:10]
    try:
        return date.fromisoformat({4: f"{text}-01-01", 7: f"{text}-01"}.get(len(text), text))
    except ValueError:
        return None


def _extend(items: list[str], more: Iterable[str]) -> None:
    items.extend(x for x in more if x and x not in items)
