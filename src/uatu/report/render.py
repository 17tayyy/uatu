"""The report as Markdown, in Spanish."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from uatu.ais.analysis import describe
from uatu.ais.events import Event
from uatu.report.data import MADRID, Listed, Report

MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
STATUS = {
    "sanctioned": "Sancionado",
    "sanctioned_owner": "Armador o gestor sancionado",
    "watchlisted": "En listas de vigilancia",
}
STOP = {"berth": "en puerto", "anchorage": "fondeado", "drifting": "parado mar adentro"}
EVENTS = {
    "ais_gap": "Apagones de AIS",
    "encounter": "Encuentros en el mar",
    "loitering": "Merodeos",
    "infrastructure_loitering": "Barcos sobre cables o tuberías",
    "sanctioned_vessel": "Barcos sancionados en la zona",
    "sanctioned_owner": "Barcos de armadores o gestores sancionados",
    "watchlisted_vessel": "Barcos en listas de vigilancia",
    "listed_stop": "Paradas de barcos sancionados o vigilados",
    "identity_change": "Cambios de identidad",
    "shared_imo": "IMO duplicados",
    "position_jump": "Saltos de posición",
    "position_on_land": "Posiciones en tierra",
    "shared_mmsi": "MMSI compartidos",
    "odd_mmsi": "MMSI imposibles",
    "emergency_beacon": "Balizas de emergencia",
}
COUNTRY = {"Spain": "España", "Morocco": "Marruecos", "Algeria": "Argelia", "France": "Francia", "Italy": "Italia"}
MAX_EVENTS = 40
LISTED_KINDS = ("sanctioned_vessel", "watchlisted_vessel", "sanctioned_owner", "listed_stop")  # said in §1 already


def number(n: float, decimals: int = 0) -> str:
    """Spanish style: 1.234,5."""
    text = f"{n:,.{decimals}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def plural(n: int, one: str, many: str) -> str:
    return f"{number(n)} {one if n == 1 else many}"


def when(t: datetime) -> str:
    local = t.astimezone(MADRID)
    return f"{local.day} {MONTHS[local.month - 1]} {local:%H:%M}"


def day(t: datetime) -> str:
    local = t.astimezone(MADRID)
    return f"{local.day} {MONTHS[local.month - 1]}"


def span(a: datetime, b: datetime) -> str:
    same_day = a.astimezone(MADRID).date() == b.astimezone(MADRID).date()
    return f"{when(a)}-{b.astimezone(MADRID):%H:%M}" if same_day else f"{when(a)} a {when(b)}"


def hours(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


def place(stop: dict) -> str:
    where = stop.get("place")
    if not where:
        return STOP[stop["kind"]]
    country = COUNTRY.get(stop.get("country") or "", stop.get("country"))
    where = f"{where} ({country})" if country and country not in ("España", where) else where
    match stop["kind"]:
        case "berth":
            return f"en el puerto de {where}"
        case "anchorage":
            return f"fondeado frente a {where}"
        case _:
            return f"parado mar adentro, a {number(stop.get('place_km') or 0)} km de {where}"


def render(r: Report, figures: dict[str, str]) -> str:
    out: list[str] = []
    zones = ", ".join(z.name for z in r.zones)
    out += [
        f"# Uatu · del {day(r.start)} al {day(r.end - timedelta(seconds=1))} de {r.end.astimezone(MADRID).year}",
        "",
        f"Análisis: {zones}. Generado el {when(datetime.now(UTC))}. Horas en hora peninsular española.",
        "",
    ]
    out += _summary(r)
    out += _listed(r, figures)
    out += _radar(r)
    out += _events(r)
    out += _traffic(r, figures)
    out += _heard(r)
    out += _limits(r)
    return "\n".join(out).rstrip() + "\n"


def _summary(r: Report) -> list[str]:
    possible = (r.end - r.recording_start).total_seconds() / 3600
    reports = sum(h.reports for h in r.heard)
    lost = f"; {plural(len(r.outages), 'corte', 'cortes')} de Uatu, ver §5" if r.outages else ""
    began = f" desde que empezó, el {when(r.recording_start)}," if r.recording_start > r.start else ""
    lines = [
        "## Resumen",
        "",
        f"- **{number(r.vessels_heard)} barcos** oídos por AIS en la costa, {number(reports)} posiciones."
        f" Uatu grabó{began} {number(r.recorded_hours, 1)} de {number(possible, 1)} horas{lost}.",
    ]
    if r.passes:
        lines.append(
            f"- **{len(r.passes)} pasadas** de Sentinel-1 analizadas: {number(sum(p.detections for p in r.passes))}"
            f" detecciones, {number(sum(p.matched for p in r.passes))} con su AIS,"
            f" {number(sum(p.no_coverage for p in r.passes))} sin AIS donde aisstream oye poco,"
            f" {number(sum(p.fixed for p in r.passes))} en objetos fijos y"
            f" **{plural(len(r.candidates), 'candidato', 'candidatos')}** sin AIS donde sí se oye bien."
        )
    else:
        lines.append("- Ninguna pasada de Sentinel-1 analizada en el periodo.")
    by_status = Counter(v.status for v in r.listed)
    if r.listed:
        names = ", ".join(f"*{v.name or v.mmsi}*" for v in r.listed[:8])
        more = f" y {len(r.listed) - 8} más" if len(r.listed) > 8 else ""
        lines.append(
            f"- **{plural(by_status['sanctioned'], 'barco sancionado', 'barcos sancionados')}**,"
            f" {by_status['sanctioned_owner']} de armadores o gestores sancionados y"
            f" {by_status['watchlisted']} en listas de vigilancia pasaron por la costa: {names}{more}."
        )
    east = sum(e for _, e, _ in r.transits_by_day)
    west = sum(w for _, _, w in r.transits_by_day)
    if east or west:
        lines.append(
            f"- {number(east + west)} cruces del Estrecho: {east} hacia el Mediterráneo, {west} hacia el Atlántico."
        )
    behaviour = [e for e in r.notable if e.kind not in LISTED_KINDS]
    lines += [f"- {plural(len(behaviour), 'evento notable', 'eventos notables')} en el AIS (§3).", ""]
    return lines


def _listed(r: Report, figures: dict[str, str]) -> list[str]:
    lines = ["## 1. Barcos sancionados y vigilados", ""]
    if not r.listed:
        return [*lines, "Ninguno oído en el periodo.", ""]
    lines += [
        "Por número IMO, que sigue al casco aunque cambie de nombre y de bandera. Fuentes: listas de sanciones"
        " (UE, Reino Unido, EE. UU., Suiza, Canadá, Australia...), la inteligencia ucraniana (GUR) y los vetos"
        " del Paris MoU, a través de OpenSanctions.",
        "",
    ]
    if "listed" in figures:
        lines += [f"![Derrotas y paradas de los barcos sancionados y vigilados]({figures['listed']})", ""]
        if figures.get("listed_elsewhere"):
            lines += [
                f"Fuera del mapa, en zonas que Uatu graba pero aún no analiza: {figures['listed_elsewhere']}.",
                "",
            ]
    for v in r.listed:
        lines += _vessel(v, r)
    return lines


def _vessel(v: Listed, r: Report) -> list[str]:
    facts = ", ".join(x for x in [v.ship_type, f"{v.length_m} m" if v.length_m else None] if x)
    lines = [f"### {v.name or f'MMSI {v.mmsi}'} · IMO {v.imo}", ""]
    lines.append(f"- **{STATUS.get(v.status, v.status)}**: {'; '.join(v.why)}.")
    if facts:
        lines.append(f"- {facts[0].upper()}{facts[1:]}, MMSI {v.mmsi}.")
    lines.append(f"- Oído entre el {when(v.first_heard)} y el {when(v.last_heard)}.")
    if v.transits:
        crossings = ", ".join(
            f"{when(at)} hacia el {'Mediterráneo' if d == 'east' else 'Atlántico'}" for at, d in v.transits
        )
        lines.append(f"- Cruzó el Estrecho: {crossings}.")
    for s in v.stops:
        duration = hours((s["end"] - s["start"]).total_seconds())
        lines.append(f"- {place(s)[0].upper()}{place(s)[1:]}, {duration} ({span(s['start'], s['end'])}).")
    for e in [e for e in r.notable if e.mmsi == v.mmsi and e.kind not in LISTED_KINDS]:
        title, _, _ = describe(_event(e), {e.mmsi: e.name} if e.name else {})
        lines.append(f"- Evento: {title} ({when(e.start)}).")
    return [*lines, ""]


def _radar(r: Report) -> list[str]:
    lines = ["## 2. Radar (Sentinel-1)", ""]
    if not r.passes:
        return [*lines, "Ninguna pasada analizada en el periodo.", ""]
    columns = ["Pasada", "Zona", "Zona cubierta", "Detecciones", "Con AIS", "Sin AIS, mala recepción",
               "Objetos fijos", "Candidatos"]  # fmt: skip
    lines += [f"| {' | '.join(columns)} |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    names = {z.key: z.name for z in r.zones}
    for p in r.passes:
        lines.append(
            f"| {when(p.acquired_at)} · {p.platform} | {names.get(p.zone, p.zone)} | {number(p.coverage * 100)} % |"
            f" {p.detections} | {p.matched} | {p.no_coverage} | {p.fixed} | {p.candidates} |"
        )
    lines.append("")
    if r.candidates:
        lines += ["**Candidatos** (detecciones sin AIS donde aisstream oye bien a los barcos de alrededor):", ""]
        for c in r.candidates:
            size = f"~{number(c.length_m)} m"
            if c.kind == "went_silent":
                silent = f", {hours((c.silent_minutes or 0) * 60)} sin emitir" if c.silent_minutes else ""
                lines.append(
                    f"- {when(c.seen_at)} · {size} · donde pudo estar un barco que dejó de emitir:"
                    f" *{c.suspect}*{silent} · {c.lat:.2f}, {c.lon:.2f}."
                )
            else:
                lines.append(
                    f"- {when(c.seen_at)} · {size} · sin identificar · hacia {c.lat:.1f}, {c.lon:.1f}"
                    " (posición redondeada a ~10 km)."
                )
        lines += [
            "",
            "Un candidato no es un barco oscuro confirmado: puede ser un barco que emitía y no se oyó, o un eco"
            " que no es un barco. Las fotos de Sentinel-2 de cada sitio están en el dashboard.",
            "",
        ]
    else:
        lines += [
            "Ningún candidato: todo lo detectado tiene AIS, está donde aisstream oye poco o es un objeto fijo.",
            "",
        ]
    return lines


def _events(r: Report) -> list[str]:
    lines = ["## 3. Comportamiento en el AIS", ""]
    notable = [e for e in r.notable if e.kind not in LISTED_KINDS]  # the listed vessels: §1
    if not notable:
        return [*lines, "Ningún evento notable en el periodo.", ""]
    counts = Counter(e.kind for e in notable)
    lines += ["| Evento | Notables o alertas |", "|---|---:|"]
    lines += [f"| {EVENTS.get(kind, kind)} | {n} |" for kind, n in counts.most_common()]
    lines.append("")
    shown = sorted(notable, key=lambda e: (-e.severity, e.start))[:MAX_EVENTS]
    for e in sorted(shown, key=lambda e: e.start):
        title, message, _ = describe(_event(e), {e.mmsi: e.name} if e.name else {})
        mark = "**Alerta**" if e.severity >= 3 else "Notable"
        lines.append(f"- {mark} · {when(e.start)} · {title}. {message}")
    if len(notable) > MAX_EVENTS:
        lines.append(f"- … y {len(notable) - MAX_EVENTS} más, en el dashboard.")
    return [*lines, ""]


def _traffic(r: Report, figures: dict[str, str]) -> list[str]:
    lines = ["## 4. Tráfico", ""]
    if r.transits_by_day:
        lines += [
            "**Cruces del Estrecho** (de Tarifa, 5°36' O, a 5°27' O o al revés). Los que van hacia el Atlántico"
            " se cuentan de menos: aisstream oye poco al oeste de Tarifa, donde se confirman.",
            "",
        ]
        if "transits" in figures:
            lines += [f"![Cruces del Estrecho por día]({figures['transits']})", ""]
    if "density" in figures:
        lines += [
            "**Dónde se concentra el tráfico**: barco-horas por celda de ~2 km (un barco cuenta una vez por hora"
            " en cada celda, emita lo que emita). Donde no hay nada puede no haber barcos o no haber recepción.",
            "",
            f"![Densidad del tráfico]({figures['density']})",
            "",
        ]
    if r.stops_by_place:
        lines += [
            "**Dónde se paran los barcos** (paradas de una hora o más en las zonas analizadas):",
            "",
            "| Sitio | Tipo | Paradas | Barcos | Barco-horas |",
            "|---|---|---:|---:|---:|",
        ]
        for s in r.stops_by_place[:12]:
            where = s["place"] or "lejos de cualquier puerto"
            lines.append(f"| {where} | {STOP[s['kind']]} | {s['stops']} | {s['vessels']} | {number(s['hours'] or 0)} |")
        lines.append("")
    return lines


def _heard(r: Report) -> list[str]:
    lines = [
        "## 5. Lo que Uatu oyó",
        "",
        "AIS recibido por los voluntarios de aisstream.io, que solo da el presente: lo que Uatu no grabó se perdió.",
        "",
        "| Zona | Posiciones | Barcos |",
        "|---|---:|---:|",
    ]
    lines += [f"| {h.zone} | {number(h.reports)} | {number(h.vessels)} |" for h in r.heard]
    lines.append("")
    if r.outages:
        lines += ["**Cortes de Uatu** (sin conexión o grabador parado, 5 minutos o más):", ""]
        lines += [f"- {span(a, b)} ({hours((b - a).total_seconds())})." for a, b in r.outages]
        lines += [
            "",
            "Un barco que deja de oírse durante un corte no ha dejado de emitir: esos apagones no cuentan.",
            "",
        ]
    return lines


def _limits(r: Report) -> list[str]:
    return [
        "## Límites",
        "",
        "- **Sin AIS no es oscuro, ni oscuro es ilícito.** aisstream oye bien los carriles del Estrecho y mal sus"
        " extremos y gran parte de la costa; muchos barcos pequeños no llevan AIS y quedan por debajo de lo que"
        " Sentinel-1 distingue a 20 m.",
        "- **Las listas no son sentencias.** Estar en una lista de sanciones o de vigilancia (GUR es parte en una"
        " guerra) dice lo que dice esa lista; un gestor sancionado no hace ilegal cada viaje de su flota.",
        "- **Las cifras de tráfico son un mínimo**, no un censo: dependen de lo que aisstream oye.",
        "- **Uso responsable**: las detecciones sin identificar se dan a ~10 km; nada se publica en tiempo real.",
        "",
        "Cómo funciona cada paso, y por qué: [docs/how-it-works.md](https://github.com/17tayyy/uatu/blob/main/docs/how-it-works.md).",
    ]


def _event(e) -> Event:
    return Event(e.kind, e.severity, e.mmsi, e.start, e.lat, e.lon, e.details, e.end)
