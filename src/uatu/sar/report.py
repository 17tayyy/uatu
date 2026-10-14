"""Outputs of a processed scene: GeoJSON for GIS tools and a quicklook image."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, reproject, transform

from uatu.sar.detect import Detection
from uatu.sar.match import MatchResult
from uatu.sar.pipeline import SceneOutput
from uatu.sar.raster import Grid

if TYPE_CHECKING:
    from uatu.sar.store import Candidate, SceneReport

WEB_MERCATOR = "EPSG:3857"


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
            if (c := result.ais_nearby.get(d)) is not None:
                properties |= {"ais_vessels_nearby": c.vessels, "ais_reports_nearby": c.reports}
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


def backscatter_overlay(output: SceneOutput, png_path: Path, corners_path: Path) -> None:
    """The scene's coarse VV backscatter as a map overlay: see `write_overlay`."""
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(output.vv)
    grey = np.clip((db + 25) / 25 * 255, 0, 255)  # -25 dB (calm sea) .. 0 dB (ships, land); NaN outside the scene
    write_overlay(grey, output.coarse, png_path, corners_path)


def write_overlay(grey: np.ndarray, grid: Grid, png_path: Path, corners_path: Path) -> None:
    """`grey` (0-255 on `grid`, NaN where transparent) as a PNG in Web Mercator, plus the lon/lat of its
    corners (top-left, top-right, bottom-right, bottom-left).

    Web maps stretch an image linearly between its corners in Web Mercator. Left in UTM, the image would
    land up to ~700 m off across the Strait, ships beside their detections rather than under them.
    """
    left, bottom, right, top = grid.bounds
    dst_transform, width, height = calculate_default_transform(
        grid.crs, WEB_MERCATOR, grid.width, grid.height, left, bottom, right, top
    )
    mercator = np.full((height, width), np.nan, dtype=np.float32)
    reproject(
        grey.astype(np.float32),
        mercator,
        src_transform=grid.transform,
        src_crs=grid.crs,
        src_nodata=np.nan,
        dst_transform=dst_transform,
        dst_crs=WEB_MERCATOR,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    alpha = np.where(np.isfinite(mercator), 255, 0)
    la = np.dstack([np.nan_to_num(mercator), alpha]).astype(np.uint8)
    Image.fromarray(la, mode="LA").save(png_path, optimize=True)

    x0, y0 = dst_transform.c, dst_transform.f
    x1, y1 = x0 + width * dst_transform.a, y0 + height * dst_transform.e
    lons, lats = transform(WEB_MERCATOR, "EPSG:4326", [x0, x1, x1, x0], [y0, y0, y1, y1])
    corners_path.write_text(json.dumps([[round(lon, 6), round(lat, 6)] for lon, lat in zip(lons, lats, strict=True)]))


def radar_image(source: Path, path: Path, block: int = 512) -> None:
    """The backscatter written by the pipeline (grey or false colour levels on the scene's UTM grid, 0 where no
    data) as a tiled GeoTIFF in Web Mercator, with overviews: what a map server cuts into tiles at any zoom.

    JPEG-compressed with a lossless mask for where there is data: 10-25 MB for a frame at 20 m.
    """
    with (
        rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True, GDAL_CACHEMAX=64),
        rasterio.open(source) as src,
        WarpedVRT(src, crs=WEB_MERCATOR, resampling=Resampling.bilinear, src_nodata=0, nodata=0) as vrt,
    ):
        colour = src.count == 3
        profile = {
            "driver": "GTiff", "width": vrt.width, "height": vrt.height, "count": src.count, "dtype": "uint8",
            "crs": WEB_MERCATOR, "transform": vrt.transform, "tiled": True, "blockxsize": block,
            "blockysize": block, "compress": "JPEG", "jpeg_quality": 85,
            **({"photometric": "YCBCR", "interleave": "pixel"} if colour else {}),
        }  # fmt: skip
        with rasterio.open(path, "w", **profile) as dst:
            for _, window in dst.block_windows(1):
                levels = vrt.read(window=window)
                has_data = (levels > 0).any(axis=0)
                if not has_data.any():
                    continue
                dst.write(levels, window=window)
                dst.write_mask(np.where(has_data, 255, 0).astype(np.uint8), window=window)
        with rasterio.open(path, "r+") as dst:
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.average)


def _point(lon: float, lat: float, properties: dict) -> dict:
    clean = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in properties.items()}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": clean}


def _write(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, default=str, indent=1))


def _listed(c: Candidate) -> str:
    if c.suspect_sanctioned:
        return f" ⚠ SANCIONADO ({c.suspect_sanctioned})"
    if c.suspect_risk == "sanctioned_owner":
        return " ⚠ ARMADOR O GESTOR SANCIONADO"
    return " ⚠ EN LISTAS DE VIGILANCIA" if c.suspect_risk == "watchlisted" else ""


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else plural or singular + 's'}"


def scene_notification(r: SceneReport, late: bool = False) -> tuple[str, str, str, str | None]:
    """(title, message, priority, link) summarising a processed pass, in Spanish.

    late: the report is on a frame that came in after the rest of the pass was announced.
    """
    interesting = [c for c in r.candidates if c.kind != "reception_gap"]
    gaps = len(r.candidates) - len(interesting)
    when = f"{r.name[:3]} {r.acquired:%d/%m %H:%M} UTC" + (f" · {r.zone}" if r.zone else "")
    when += " (toma tardía)" if late else ""
    headline = f"{_count(len(interesting), 'barco')} sin AIS" if interesting else "sin barcos oscuros"
    title = f"Pasada {when}: {headline}"
    frames = f", {r.frames} tomas" if r.frames > 1 else ""
    lines = [
        f"{r.detections} barcos detectados ({r.coverage:.0%} de la zona{frames}): {r.matched} con AIS,"
        f" {r.no_coverage} sin AIS donde se recibe mal, {r.at_fixed_objects} en objetos fijos."
    ]
    if r.candidates:
        explained = _count(gaps, "explicado", "explicados")
        lines.append(f"{len(r.candidates)} sin AIS donde se recibe bien; {explained} por huecos de recepción.")
    for c in interesting[:6]:
        where = f"{c.lat:.4f}, {c.lon:.4f}, ~{c.length_m:.0f} m"
        if c.kind == "went_silent":
            hours, minutes = divmod(round(c.suspect_silent_minutes or 0), 60)
            name = c.suspect_name or f"MMSI {c.suspected_mmsi}"
            lines.append(
                f"• Se calló: {name}{_listed(c)}, {hours} h {minutes:02d} min sin emitir,"
                f" a {(c.suspect_distance_m or 0) / 1000:.1f} km ({where})"
            )
        else:
            lines.append(f"• Sin identificar ({where})")
    if len(interesting) > 6:
        lines.append(f"… y {len(interesting) - 6} más")
    urgent = any(c.suspect_sanctioned or c.suspect_risk == "sanctioned_owner" for c in interesting)
    priority = "high" if urgent else "default"
    link = None
    if interesting:
        c = interesting[0]
        link = f"https://www.openstreetmap.org/?mlat={c.lat:.5f}&mlon={c.lon:.5f}#map=12/{c.lat:.4f}/{c.lon:.4f}"
    return title, "\n".join(lines), priority, link
