"""One pass of AIS analysis: run every detector over the recent record and store what they find."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.ais import store
from uatu.ais.bathymetry import Bathymetry
from uatu.ais.coast import CoastDistance
from uatu.ais.events import (
    DEFAULT_PARAMS,
    Event,
    LocalProjection,
    Params,
    detect_encounters,
    detect_gaps,
    detect_identity_changes,
    detect_infrastructure_loitering,
    detect_jumps,
    detect_loitering,
    downgrade_in_crowds,
)
from uatu.config import BBox

WINDOW = timedelta(hours=6)  # long enough for the longest pattern (3 h loitering) plus margin
IDENTITY_HISTORY = timedelta(days=7)


@dataclass(frozen=True)
class Outcome:
    new: list[Event]
    detected: int  # all events found this run, including ones already stored
    tracks: int
    gaps_closed: int


async def analyse(
    engine: AsyncEngine,
    now: datetime,
    bbox: BBox,
    coast: CoastDistance,
    p: Params = DEFAULT_PARAMS,
    bathymetry: Bathymetry | None = None,
) -> Outcome:
    start = now - WINDOW
    lat_min, lon_min, lat_max, lon_max = bbox
    proj = LocalProjection((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
    tracks = await store.tracks(engine, start, now, bbox)
    infrastructure = await store.infrastructure_lines(engine, proj)
    t0, t1 = start.timestamp(), now.timestamp()

    events = [
        *detect_gaps(tracks, t1, bbox, coast.at, proj, p),
        *detect_encounters(tracks, t0, t1, coast.at, proj, p),
        *detect_loitering(tracks, coast.at, p),
        *detect_infrastructure_loitering(
            tracks, infrastructure, coast.at, proj, p, bathymetry.depth_at if bathymetry else None
        ),
        *detect_jumps(tracks, proj, p),
    ]
    stopped = await store.stopped_places(engine, now - timedelta(days=p.crowd_history_days))
    events = downgrade_in_crowds(events, stopped, proj, p)
    reports = await store.static_reports(engine, now - IDENTITY_HISTORY)
    events += detect_identity_changes(reports, start, await store.last_positions(engine, start))
    events += await store.sanctioned_present(engine, start, now)
    events += await store.shared_imos(engine, start, now)

    closed = await store.close_resumed_gaps(engine)
    new = await store.save(engine, events)
    return Outcome(new, len(events), len(tracks), closed)


# --- alerts ----------------------------------------------------------------------------------------


def worth_alerting(e: Event, now: datetime) -> bool:
    """Notable and still current: a first run over hours of history should not flood the phone."""
    return e.severity >= 2 and (e.end is None or e.end >= now - timedelta(hours=1))


def describe(e: Event, names: dict[int, str]) -> tuple[str, str, str]:
    """(title, message, link) for a notification, in Spanish."""
    d = e.details
    name = names.get(e.mmsi) or d.get("name") or f"MMSI {e.mmsi}"
    other = names.get(e.other_mmsi or 0) or d.get("other_name") or f"MMSI {e.other_mmsi}"
    where = f"{e.lat:.4f}, {e.lon:.4f}"
    link = f"https://www.openstreetmap.org/?mlat={e.lat:.5f}&mlon={e.lon:.5f}#map=12/{e.lat:.4f}/{e.lon:.4f}"
    match e.kind:
        case "ais_gap":
            title = f"AIS apagado: {name}"
            message = (
                f"{name} (MMSI {e.mmsi}) lleva {d['silent_minutes']} min sin emitir, a {d['coast_km']} km de la costa,"
                f" mientras se oían {d['ais_heard_nearby']} barcos alrededor. Última posición {where}."
            )
        case "encounter":
            title = f"Encuentro en el mar: {name} y {other}"
            message = (
                f"Llevan {d['hours']} h casi parados a {d['min_distance_m']} m el uno del otro,"
                f" a {d['coast_km']} km de la costa ({where})."
            )
        case "loitering":
            title = f"Merodeo: {name}"
            message = f"{name} lleva {d['hours']} h casi parado a {d['coast_km']} km de la costa ({where})."
        case "infrastructure_loitering":
            title = f"Barco sobre {d['infrastructure']}: {name}"
            message = (
                f"{name} (MMSI {e.mmsi}) lleva {d['minutes']} min a menos de 3 nudos y a {d['min_distance_m']} m"
                f" de {d['infrastructure']} ({d['infrastructure_kind']})"
                + (f", en {d['depth_m']} m de agua" if d.get("depth_m") is not None else "")
                + f". Posición {where}."
            )
        case "sanctioned_vessel":
            title = f"Barco sancionado en la zona: {name}"
            message = (
                f"{name} (MMSI {e.mmsi}, IMO {d['imo']}) figura en listas de sanciones como {d['listed_as']}"
                f" ({d['programs']}). Última posición {where}."
            )
        case "identity_change":
            title = f"Cambio de identidad: {name}"
            message = f"MMSI {e.mmsi} ha cambiado su {d['field']}: {d['before']} → {d['after']}."
        case "shared_imo":
            title = f"IMO duplicado: {d['imo']}"
            message = f"{name} (MMSI {e.mmsi}) y {other} (MMSI {e.other_mmsi}) emiten el mismo IMO {d['imo']}."
        case _:
            title, message = f"{e.kind}: {name}", f"{d} ({where})"
    return title, message, link


def digest(
    events: list[Event], names: dict[int, str], hours: int, gaps: list[tuple[datetime, datetime]] = ()
) -> tuple[str, str]:
    """A summary notification of notable events and of the periods Uatu recorded no AIS."""
    labels = {
        "ais_gap": "apagones de AIS",
        "encounter": "encuentros en el mar",
        "loitering": "merodeos",
        "infrastructure_loitering": "barcos sobre infraestructura",
        "sanctioned_vessel": "barcos sancionados",
        "identity_change": "cambios de identidad",
        "shared_imo": "IMO duplicados",
    }
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    lines = []
    if gaps:
        blind = sum((end - start for start, end in gaps), timedelta())
        spans = ", ".join(f"{start:%H:%M}-{end:%H:%M}" for start, end in gaps)
        lines.append(f"⚠ Sin AIS {blind.total_seconds() / 60:.0f} min ({spans} UTC)")
    if events:
        lines.append(", ".join(f"{n} {labels.get(kind, kind)}" for kind, n in counts.items()))
        lines += [f"• {describe(e, names)[0]}" for e in events[:8]]
        if len(events) > 8:
            lines.append(f"… y {len(events) - 8} más")
    return f"Uatu: resumen AIS de las últimas {hours} h", "\n".join(lines)
