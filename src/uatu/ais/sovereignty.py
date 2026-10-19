"""Foreign state vessels in Spain's waters, and in the waters Spain and another state both claim.

A state vessel is one AIS says is military (type 35) or law enforcement (55), or named as one (NAVY,
HMS, COAST GUARD, GENDARMERIE...). Warships need not broadcast AIS, and most do not: Morocco's navy and
gendarmerie were never heard in Uatu's first week. What is told here is only what broadcasts.

Passing is a right: foreign warships have innocent passage through a territorial sea, and transit
passage through the Strait. So a stay is told for what it was: where, how long, how much of it nearly
stopped. A stay in waters both Spain and the vessel's state claim, or nearly stopped in Spain's own,
is notable; passing through Spain's territorial sea, informative. And what a vessel does day after day
(the Gibraltar Squadron around the Rock) is routine, informative too: what stands out is what is new.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from uatu.ais.events import CoastFn, Event, Track
from uatu.ais.mmsi import kind as mmsi_kind

SPANISH_MIDS = {224, 225}
# Flags by Maritime Identification Digits (ITU), in Spanish: the ones that sail these waters.
FLAGS = {
    **dict.fromkeys((224, 225), "España"), 242: "Marruecos", 605: "Argelia", 672: "Túnez", 642: "Libia",
    622: "Egipto", 654: "Mauritania", 663: "Senegal", 617: "Cabo Verde",
    **dict.fromkeys(range(232, 236), "Reino Unido"), 236: "Gibraltar", **dict.fromkeys(range(226, 229), "Francia"),
    263: "Portugal", 255: "Portugal (Madeira)", 204: "Portugal (Azores)", 247: "Italia",
    **dict.fromkeys((211, 218), "Alemania"), **dict.fromkeys(range(244, 247), "Países Bajos"), 205: "Bélgica",
    **dict.fromkeys((237, 239, 240, 241), "Grecia"), 271: "Turquía", 273: "Rusia", 272: "Ucrania",
    **dict.fromkeys(range(257, 260), "Noruega"), **dict.fromkeys((219, 220), "Dinamarca"),
    **dict.fromkeys((265, 266), "Suecia"), 230: "Finlandia", 250: "Irlanda",
    **dict.fromkeys((215, 229, 248, 249, 256), "Malta"), **dict.fromkeys((209, 210, 212), "Chipre"),
    238: "Croacia", 278: "Eslovenia", 201: "Albania", 262: "Montenegro", 207: "Bulgaria", 264: "Rumanía",
    261: "Polonia", 276: "Estonia", 275: "Letonia", 277: "Lituania", 213: "Georgia", 254: "Mónaco",
    **dict.fromkeys((338, 366, 367, 368, 369), "Estados Unidos"), 316: "Canadá", 710: "Brasil", 428: "Israel",
    **dict.fromkeys((412, 413, 414), "China"), 419: "India", **dict.fromkeys((431, 432), "Japón"), 503: "Australia",
}  # fmt: skip
STATE_TYPES = {35: "military", 55: "law_enforcement"}
NAVY = re.compile(
    r"\b(NAVY|WARSHIP|MARINE ROYALE|MARINE NATIONALE|MARINA MILITARE|MARINHA)\b"
    r"|^(HMS|USS|USNS|HNLMS|HDMS|FGS|ESPS|NRP|TCG|BNS|SPS)\s"
)
LAW = re.compile(
    r"\b(COAST ?GUARD|GARDE[ -]?C[OÔ]TES?|GENDARMERIE|POLICE|POLIZIA|GUARDIA DI FINANZA|GUARDA COSTEIRA)\b"
)


@dataclass(frozen=True, slots=True)
class StateVessel:
    mmsi: int
    name: str | None
    kind: str  # military | law_enforcement
    flag: str  # in Spanish; "MID 123" for flags not in FLAGS


@dataclass(frozen=True)
class Waters:
    id: int  # Marine Regions MRGID
    name: str
    kind: str  # territorial_sea | disputed
    claimants: tuple[str, ...]
    geometry: BaseGeometry  # lon/lat


@dataclass(frozen=True, slots=True)
class Params:
    gap_s: float = 3600  # a stay goes on across silences this long
    min_s: float = 10 * 60  # shorter is a glimpse, not a stay
    slow_kn: float = 3.0
    stayed_s: float = 30 * 60  # nearly stopped this long: a stay, not a passage
    routine_days: int = 3  # in the same waters on this many other days of the past week: routine
    # Closer to the coast than this: berthed at its base (Gibraltar's police in its harbour, a patrol boat in
    # Beni Ansar, inside Melilla's claimed waters), not a stay at sea.
    in_port_m: float = 500


DEFAULT_PARAMS = Params()


def flag(mmsi: int) -> str | None:
    """The flag a ship's MMSI says, in Spanish; None for MMSIs that are not ships'."""
    if mmsi_kind(mmsi) != "ship":
        return None
    mid = int(f"{mmsi:09d}"[:3])
    return FLAGS.get(mid, f"MID {mid}")


def state_kind(name: str | None, ship_type: int | None) -> str | None:
    """military | law_enforcement, by AIS type or by name; None for any other vessel."""
    if ship_type in STATE_TYPES:
        return STATE_TYPES[ship_type]
    upper = " ".join((name or "").upper().split())
    if NAVY.search(upper):
        return "military"
    if LAW.search(upper):
        return "law_enforcement"
    return None


def foreign_state_vessels(vessels: Iterable[tuple[int, str | None, int | None]]) -> dict[int, StateVessel]:
    """Of (mmsi, name, ship type), the state vessels flying any flag but Spain's."""
    found = {}
    for mmsi, name, ship_type in vessels:
        kind = state_kind(name, ship_type)
        country = flag(mmsi)
        if kind is None or country is None or int(f"{mmsi:09d}"[:3]) in SPANISH_MIDS:
            continue
        found[mmsi] = StateVessel(mmsi, name, kind, country)
    return found


def detect_state_vessels(
    tracks: Sequence[Track],
    vessels: dict[int, StateVessel],
    waters: Sequence[Waters],
    coast: CoastFn | None = None,
    p: Params = DEFAULT_PARAMS,
) -> list[Event]:
    """A stay of each foreign state vessel in each of Spain's waters (claimed ones first, where both), at sea:
    `coast` (metres to land) tells the berthed ones."""
    ordered = sorted(waters, key=lambda w: w.kind != "disputed")
    events = []
    for tr in tracks:
        vessel = vessels.get(tr.mmsi)
        if vessel is None:
            continue
        where = np.full(len(tr.t), -1)
        if coast is not None:
            where[coast(tr.lon, tr.lat) < p.in_port_m] = -2  # NaN (unknown) counts as at sea
        for k, w in enumerate(ordered):
            where[(where == -1) & shapely.contains_xy(w.geometry, tr.lon, tr.lat)] = k
        for k in sorted(set(where.tolist()) - {-1, -2}):
            w = ordered[k]
            for first, last in _stays(tr.t, where == k, p.gap_s):
                if tr.t[last] - tr.t[first] < p.min_s:
                    continue
                events.append(_stay(tr, first, last, where == k, vessel, w, p))
    return events


def downgrade_routine(
    events: list[Event], days: dict[tuple[int, int], set[date]], p: Params = DEFAULT_PARAMS
) -> list[Event]:
    """Lower to informative the stays of vessels found in the same waters on routine_days other days.

    days: (mmsi, waters id) -> the days, of the past week, a stay of it there was found.
    """
    result = []
    for e in events:
        if e.kind == "state_vessel" and e.severity > 1:
            others = days.get((e.mmsi, e.details["waters_id"]), set()) - {e.start.astimezone(UTC).date()}
            if len(others) >= p.routine_days:
                e = replace(e, severity=1, details=e.details | {"routine_days": len(others)})
        result.append(e)
    return result


def _stays(t: np.ndarray, inside: np.ndarray, gap_s: float) -> Iterable[tuple[int, int]]:
    """(first, last) of the fixes inside, as stays broken by silences or excursions longer than gap_s."""
    idx = np.flatnonzero(inside)
    if not idx.size:
        return
    start = prev = int(idx[0])
    for i in idx[1:].tolist():
        if t[i] - t[prev] > gap_s:
            yield start, prev
            start = i
        prev = i
    yield start, prev


def _stay(tr: Track, first: int, last: int, inside: np.ndarray, vessel: StateVessel, w: Waters, p: Params) -> Event:
    span = slice(first, last + 1)
    t, sog, mask = tr.t[span], tr.sog[span], inside[span]
    # Time nearly stopped: between consecutive fixes inside, both under slow_kn.
    slow = (sog[:-1] < p.slow_kn) & (sog[1:] < p.slow_kn) & mask[:-1] & mask[1:]
    slow_s = float(np.sum(np.diff(t)[slow]))
    stayed = slow_s >= p.stayed_s
    known = sog[mask & ~np.isnan(sog)]
    details = {
        "name": vessel.name,
        "flag": vessel.flag,
        "vessel_kind": vessel.kind,
        "waters": w.name,
        "waters_id": w.id,
        "waters_kind": w.kind,
        "claimants": list(w.claimants),
        "minutes": round((t[-1] - t[0]) / 60),
        "slow_minutes": round(slow_s / 60),
        "mean_sog": round(float(np.mean(known)), 1) if known.size else None,
    }
    severity = 2 if w.kind == "disputed" or stayed else 1
    return Event(
        "state_vessel",
        severity,
        tr.mmsi,
        datetime.fromtimestamp(float(t[0]), UTC),
        float(np.mean(tr.lat[span][mask])),
        float(np.mean(tr.lon[span][mask])),
        details,
        datetime.fromtimestamp(float(t[-1]), UTC),
    )


def stays_by_day(rows: Iterable[tuple[int, int, date]]) -> dict[tuple[int, int], set[date]]:
    """(mmsi, waters id, day) rows as (mmsi, waters id) -> days."""
    days: dict[tuple[int, int], set[date]] = defaultdict(set)
    for mmsi, waters_id, day in rows:
        days[mmsi, waters_id].add(day)
    return dict(days)
