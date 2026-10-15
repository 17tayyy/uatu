"""One pass of AIS analysis: run every detector over the recent record and store what they find."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
    downgrade_when_blind,
    downgrade_where_usually_silent,
)
from uatu.ais.sanctions import explain
from uatu.ais.stops import Stop, detect_stops, detect_transits
from uatu.config import BBox

WINDOW = timedelta(hours=6)  # long enough for the longest pattern (3 h loitering) plus margin
IDENTITY_HISTORY = timedelta(days=7)


@dataclass(frozen=True)
class Outcome:
    new: list[Event]
    detected: int  # all events found this run, including ones already stored
    tracks: int
    gaps_closed: int
    stops: int = 0  # vessels found stopped (most of them already known)
    transits: int = 0  # new crossings of the Strait


@dataclass(frozen=True)
class Area:
    """A zone as the track detectors need it."""

    bbox: BBox
    coast: CoastDistance
    bathymetry: Bathymetry | None = None  # without it, every vessel over a cable is an alert
    zone: str | None = None  # its key, recorded with the stops found in it


async def analyse(engine: AsyncEngine, now: datetime, areas: list[Area], p: Params = DEFAULT_PARAMS) -> Outcome:
    """Track detectors zone by zone (they need its coast and seabed); the rest once, everywhere recorded."""
    start = now - WINDOW
    t0, t1 = start.timestamp(), now.timestamp()
    stopped = await store.stopped_places(engine, now - timedelta(days=p.crowd_history_days))
    quiet = await store.silences(engine, now - timedelta(days=p.crowd_history_days))
    events: list[Event] = []
    tracked = stopped_now = crossed = 0
    ports = await store.ports(engine)
    for area in areas:
        lat_min, lon_min, lat_max, lon_max = area.bbox
        proj = LocalProjection((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
        tracks = await store.tracks(engine, start, now, area.bbox)
        infrastructure = await store.infrastructure_lines(engine, proj)
        depth = area.bathymetry.depth_at if area.bathymetry else None
        found = [
            *detect_gaps(tracks, t1, area.bbox, area.coast.at, proj, p),
            *detect_encounters(tracks, t0, t1, area.coast.at, proj, p),
            *detect_loitering(tracks, area.coast.at, p),
            *detect_infrastructure_loitering(tracks, infrastructure, area.coast.at, proj, p, depth),
            *detect_jumps(tracks, proj, p),
        ]
        events += downgrade_where_usually_silent(downgrade_in_crowds(found, stopped, proj, p), quiet, proj, p)
        tracked += len(tracks)
        stops = await store.save_stops(engine, detect_stops(tracks, area.coast.at, ports, proj), area.zone)
        events += await listed_stops(engine, stops)
        stopped_now += len(stops)
        crossed += await store.save_transits(engine, detect_transits(tracks))
    reports = await store.static_reports(engine, now - IDENTITY_HISTORY)
    events += detect_identity_changes(reports, start, await store.last_positions(engine, start), now)
    events += await store.sanctioned_present(engine, start, now)
    events += await store.network_present(engine, start, now)
    events += await store.shared_imos(engine, start, now)

    # Uatu deaf for 5 minutes or more (its connection, the recorder): no silence then says anything.
    blind = await store.recording_gaps(engine, start - timedelta(seconds=p.gap_min_s), timedelta(minutes=5))
    events = downgrade_when_blind(events, blind, p)
    closed = await store.close_resumed_gaps(engine)
    new = await store.save(engine, events)
    return Outcome(new, len(events), tracked, closed, stopped_now, crossed)


STOPPED = {"berth": "en puerto", "anchorage": "fondeado", "drifting": "parado mar adentro"}


async def listed_stops(engine: AsyncEngine, stops: list[Stop]) -> list[Event]:
    """Sanctioned and watchlisted vessels stopping here: in a port (an alert, for one sanctioned or run
    by a sanctioned company) or at anchor, where bunkering and transfers happen."""
    statuses = await store.listed(engine, {s.mmsi for s in stops})
    events = []
    for s in stops:
        if s.mmsi not in statuses:
            continue
        status, name = statuses[s.mmsi]
        severity = 3 if s.kind == "berth" and status in ("sanctioned", "sanctioned_owner") else 2
        details = {
            "name": name,
            "listed": status,
            "stop": s.kind,
            "place": s.place.name if s.place else None,
            "place_country": s.place.country if s.place else None,
            "place_km": round(s.place_m / 1000, 1) if s.place_m is not None else None,
            "hours": s.hours,
        }
        events.append(Event("listed_stop", severity, s.mmsi, s.start, s.lat, s.lon, details, s.end))
    return events


def stop_place(d: dict) -> str:
    """'fondeado frente a Ceuta', 'en puerto (Algeciras)', 'parado mar adentro, a 40 km de Almería'."""
    stop, place = d["stop"], d.get("place")
    if not place:
        return STOPPED[stop]
    if stop == "berth":
        return f"en el puerto de {place}"
    if stop == "anchorage":
        return f"fondeado frente a {place}"
    return f"parado mar adentro, a {d['place_km']:.0f} km de {place}"


# --- alerts ----------------------------------------------------------------------------------------


def worth_alerting(e: Event, now: datetime) -> bool:
    """Notable and still current: a first run over hours of history should not flood the phone."""
    return e.severity >= 2 and (e.end is None or e.end >= now - timedelta(hours=1))


def describe(e: Event, names: dict[int, str]) -> tuple[str, str, str]:
    """(title, message, link) for a notification, in Spanish."""
    d = e.details
    named = names.get(e.mmsi) or d.get("name")
    name = named or f"MMSI {e.mmsi}"
    who = f"{named} (MMSI {e.mmsi})" if named else name  # not "MMSI 1 (MMSI 1)"
    other = names.get(e.other_mmsi or 0) or d.get("other_name") or f"MMSI {e.other_mmsi}"
    where = f"{e.lat:.4f}, {e.lon:.4f}"
    link = f"https://www.openstreetmap.org/?mlat={e.lat:.5f}&mlon={e.lon:.5f}#map=12/{e.lat:.4f}/{e.lon:.4f}"
    match e.kind:
        case "ais_gap":
            title = f"AIS apagado: {name}"
            message = (
                f"{who} {_silence(e)}, a {d['coast_km']} km de la costa,"
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
                f"{who} lleva {d['minutes']} min a menos de 3 nudos y a {d['min_distance_m']} m"
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
        case "sanctioned_owner":
            title = f"Barco con armador o gestor sancionado: {name}"
            message = (
                f"{name} (MMSI {e.mmsi}, IMO {d['imo']}) no figura en listas de sanciones, pero"
                f" {'; '.join(explain(r) for r in d['reasons'])}. Última posición {where}."
            )
        case "watchlisted_vessel":
            title = f"Barco en listas de vigilancia: {name}"
            message = (
                f"{name} (MMSI {e.mmsi}, IMO {d['imo']}): {'; '.join(explain(r) for r in d['reasons'])}."
                f" Última posición {where}."
            )
        case "listed_stop":
            status = {"sanctioned": "sancionado", "sanctioned_owner": "con gestor sancionado"}.get(
                d["listed"], "vigilado"
            )
            title = f"Barco {status} {stop_place(d)}: {name}"
            ongoing = e.end is None or e.end > datetime.now(UTC) - timedelta(minutes=30)
            message = f"{who} {'lleva' if ongoing else 'estuvo'} {d['hours']} h {stop_place(d)}."
            message += f" Posición {where}."
        case "identity_change":
            title = f"Cambio de identidad: {name}"
            message = f"MMSI {e.mmsi} ha cambiado su {d['field']}: {d['before']} → {d['after']}."
        case "shared_imo":
            title = f"IMO duplicado: {d['imo']}"
            message = f"{who} y {other} (MMSI {e.other_mmsi}) emiten el mismo IMO {d['imo']}."
        case "position_jump":
            title = f"Salto de posición: {name}"
            message = (
                f"{who} apareció {d['jump_km']} km más allá, a {d['implied_knots']} nudos"
                f" aparentes: GPS falseado o averiado. Desde {where}."
            )
        case _:
            title, message = f"{e.kind}: {name}", f"{d} ({where})"
    return title, message, link


def _silence(e: Event) -> str:
    """How long a gap lasted, or lasts, as far as Uatu knows."""
    minutes = e.details["silent_minutes"]
    if e.end is not None:
        return f"estuvo {minutes} min sin emitir"
    if datetime.now(UTC) - e.start > WINDOW:  # out of the analysis' sight: not heard again since
        return "dejó de oírse y no se le ha vuelto a oír"
    return f"lleva {minutes} min sin emitir"


def digest(
    events: list[Event], names: dict[int, str], hours: int, gaps: list[tuple[datetime, datetime]] = ()
) -> tuple[str, str]:
    """A summary notification of notable events and of the periods Uatu recorded no AIS."""
    labels = {  # kind: (one, several)
        "ais_gap": ("apagón de AIS", "apagones de AIS"),
        "encounter": ("encuentro en el mar", "encuentros en el mar"),
        "loitering": ("merodeo", "merodeos"),
        "infrastructure_loitering": ("barco sobre infraestructura", "barcos sobre infraestructura"),
        "sanctioned_vessel": ("barco sancionado", "barcos sancionados"),
        "sanctioned_owner": ("barco de armador o gestor sancionado", "barcos de armadores o gestores sancionados"),
        "watchlisted_vessel": ("barco en listas de vigilancia", "barcos en listas de vigilancia"),
        "listed_stop": ("parada de un barco vigilado", "paradas de barcos vigilados"),
        "identity_change": ("cambio de identidad", "cambios de identidad"),
        "shared_imo": ("IMO duplicado", "IMO duplicados"),
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
        lines.append(", ".join(f"{n} {labels.get(kind, (kind, kind))[n > 1]}" for kind, n in counts.items()))
        lines += [f"• {describe(e, names)[0]}" for e in events[:8]]
        if len(events) > 8:
            lines.append(f"… y {len(events) - 8} más")
    return f"Uatu: resumen AIS de las últimas {hours} h", "\n".join(lines)
