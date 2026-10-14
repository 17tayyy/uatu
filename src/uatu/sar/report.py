"""Outputs of a processed scene: GeoJSON for GIS tools and a quicklook image."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from uatu.sar.detect import Detection
from uatu.sar.match import MatchResult
from uatu.sar.pipeline import SceneOutput

if TYPE_CHECKING:
    from uatu.sar.store import SceneReport


def detections_geojson(detections: list[Detection], result: MatchResult | None, path: Path) -> None:
    """One point per detection, with the AIS vessel it was paired with (if any)."""
    paired = {id(m.detection): m for m in result.matches} if result else {}
    features = []
    for d in detections:
        properties = asdict(d)
        if (m := paired.get(id(d))) is not None:
            v = m.expected.vessel
            properties |= {"mmsi": v.mmsi, "ais_name": v.name, "ais_length_m": v.length_m, "distance_m": m.distance_m}
        properties["contrast_db"] = round(d.contrast_db, 1)
        if result is not None:
            properties["status"] = "matched" if id(d) in paired else "dark" if d in result.dark else "no_coverage"
            if d in result.ais_nearby:
                properties["ais_vessels_nearby"] = result.ais_nearby[d]
        features.append(_point(d.lon, d.lat, properties))
    _write(path, features)


def ais_geojson(result: MatchResult, path: Path) -> None:
    """One point per AIS vessel at acquisition time, with whether the radar detected it."""
    features = []
    groups = [
        ("detected", [m.expected for m in result.matches]),
        ("undetected", result.undetected),
        ("not_imaged", result.not_imaged),
    ]
    for status, group in groups:
        for e in group:
            properties = {k: v for k, v in asdict(e.vessel).items() if k not in ("lon", "lat")}
            properties |= {"status": status, "seen_at": e.vessel.seen_at.isoformat(), "doppler_shift_m": e.shift_m}
            features.append(_point(e.vessel.lon, e.vessel.lat, properties))
    _write(path, features)


def quicklook(output: SceneOutput, result: MatchResult | None, title: str, path: Path) -> None:
    """VV backscatter with detections (green: AIS match, red: dark, orange: no AIS coverage to tell)
    and AIS vessels the radar missed (yellow)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(output.vv)
    height, width = db.shape
    fig, ax = plt.subplots(figsize=(16, 16 * height / width), dpi=120)
    ax.imshow(db, cmap="gray", vmin=-25, vmax=0, interpolation="nearest")
    ax.imshow(np.ma.masked_where(~output.land, output.land), cmap="Greens", alpha=0.3, vmin=0, vmax=2)

    inv = ~output.coarse.transform

    def pixel(x: float, y: float) -> tuple[float, float]:
        col, row = inv @ (x, y)
        return col - 0.5, row - 0.5  # imshow puts pixel centres on integers

    if result is None:
        for d in output.detections:
            ax.add_patch(plt.Circle(pixel(d.x, d.y), 10, fill=False, color="red", lw=1))
    else:
        for m in result.matches:
            ax.add_patch(plt.Circle(pixel(m.detection.x, m.detection.y), 10, fill=False, color="lime", lw=1))
        for d in result.no_coverage:
            ax.add_patch(plt.Circle(pixel(d.x, d.y), 12, fill=False, color="orange", lw=1.2))
        for d in result.dark:
            ax.add_patch(plt.Circle(pixel(d.x, d.y), 14, fill=False, color="red", lw=1.6))
        for e in result.undetected:
            ax.plot(*pixel(e.x, e.y), marker="^", color="yellow", markersize=5, markeredgewidth=0)
        counts = (
            f"{len(result.matches)} matched, {len(result.dark)} dark, {len(result.no_coverage)} without AIS coverage,"
            f" {len(result.undetected)} AIS undetected"
        )
        title = f"{title}\n{counts}"
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _point(lon: float, lat: float, properties: dict) -> dict:
    clean = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in properties.items()}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": clean}


def _write(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, default=str, indent=1))


def scene_notification(r: SceneReport) -> tuple[str, str, str, str | None]:
    """(title, message, priority, link) summarising a processed pass, in Spanish."""
    interesting = [c for c in r.candidates if c.kind != "reception_gap"]
    gaps = len(r.candidates) - len(interesting)
    when = f"{r.name[:3]} {r.acquired:%d/%m %H:%M} UTC"
    title = f"Pasada {when}: " + (f"{len(interesting)} barcos sin AIS" if interesting else "sin barcos oscuros")
    lines = [
        f"{r.detections} barcos detectados ({r.coverage:.0%} de la zona): {r.matched} con AIS,"
        f" {r.no_coverage} donde no hay cobertura AIS, {r.at_fixed_objects} en objetos fijos."
    ]
    if r.candidates:
        lines.append(f"{len(r.candidates)} sin AIS donde sí hay cobertura; {gaps} explicados por huecos de recepción.")
    for c in interesting[:6]:
        where = f"{c.lat:.4f}, {c.lon:.4f}, ~{c.length_m:.0f} m"
        if c.kind == "went_silent":
            hours, minutes = divmod(round(c.suspect_silent_minutes or 0), 60)
            name = c.suspect_name or f"MMSI {c.suspected_mmsi}"
            sanctioned = f" ⚠ SANCIONADO ({c.suspect_sanctioned})" if c.suspect_sanctioned else ""
            lines.append(
                f"• Se calló: {name}{sanctioned}, {hours} h {minutes:02d} min sin emitir,"
                f" a {(c.suspect_distance_m or 0) / 1000:.1f} km ({where})"
            )
        else:
            lines.append(f"• Sin identificar ({where})")
    if len(interesting) > 6:
        lines.append(f"… y {len(interesting) - 6} más")
    priority = "high" if any(c.suspect_sanctioned for c in interesting) else "default"
    link = None
    if interesting:
        c = interesting[0]
        link = f"https://www.openstreetmap.org/?mlat={c.lat:.5f}&mlon={c.lon:.5f}#map=12/{c.lat:.4f}/{c.lon:.4f}"
    return title, "\n".join(lines), priority, link
