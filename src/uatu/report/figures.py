"""The report's figures: light, for reading and printing."""

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle
from matplotlib.ticker import FuncFormatter
from shapely.geometry import shape

from uatu.report.data import CELL_DEG, Report
from uatu.zones import NATURAL_EARTH_LAND

LAND, SEA, EDGE, INK, MUTED = "#e4e2dc", "#f7f8fa", "#b9b6ad", "#2b2b28", "#77756d"
EAST, WEST = "#2a6fd1", "#d0782a"  # the two directions: a blue and an orange, told apart by name too
LISTED = {"sanctioned": "#c43131", "sanctioned_owner": "#c43131", "watchlisted": "#d0782a"}
# One blue from clearly visible to dark: on a white sea, the palest blues vanish.
BLUES = LinearSegmentedColormap.from_list("blues", plt.get_cmap("Blues")(np.linspace(0.3, 1, 256)))


def density_map(r: Report, path: Path) -> None:
    """Where traffic gathered: vessel-hours per ~2 km cell, on one blue scale."""
    fig, ax = _map(r, _extent([(lon, lat) for lon, lat, _ in r.density]))
    if r.density:
        lon, lat, hours = (np.array(c) for c in zip(*r.density, strict=True))
        sizes = np.full(len(lon), CELL_DEG)
        cells = ax.scatter(lon, lat, c=hours, s=0, cmap=BLUES, norm=LogNorm(vmin=1, vmax=max(hours.max(), 2)))
        for x, y, h in zip(lon - sizes / 2, lat - sizes / 2, hours, strict=True):
            ax.add_patch(Rectangle((x, y), CELL_DEG, CELL_DEG, color=cells.cmap(cells.norm(h)), lw=0, zorder=2))
        bar = fig.colorbar(
            cells, ax=ax, shrink=0.7, pad=0.01, format=FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", "."))
        )
        bar.set_label("barco-horas por celda de ~2 km", color=MUTED)
        bar.ax.tick_params(labelsize=7, colors=MUTED)
    _zones(ax, r)
    _save(fig, path)


def listed_map(r: Report, path: Path) -> list[str]:
    """The tracks of the sanctioned and watchlisted vessels in the analysed zones, numbered, and where they
    stopped. Returns the names of those heard only outside the map."""
    shown = [v for v in r.listed if any(_in_zones(p, r) for p in v.track)]
    points = [p for v in shown for p in v.track if _in_zones(p, r)]
    fig, ax = _map(r, _extent(points, pad=0.12))
    legend = []
    for n, v in enumerate(shown, start=1):
        colour = LISTED.get(v.status, MUTED)
        lon, lat = zip(*v.track, strict=True)
        ax.plot(lon, lat, color=colour, lw=1.3, alpha=0.9, zorder=3)
        # The number where it was last heard: names would pile up in the Strait.
        badge = {"boxstyle": "circle,pad=0.25", "facecolor": colour, "edgecolor": "white", "lw": 0.6}
        ax.annotate(str(n), v.track[-1], fontsize=6.5, color="white", ha="center", va="center", zorder=6, bbox=badge)
        legend.append(f"{n:>2}  {v.name or v.mmsi}")
    stops = [s for v in shown for s in v.stops]
    if stops:
        ax.scatter([s["lon"] for s in stops], [s["lat"] for s in stops], s=34, marker="s", facecolor="white",
                   edgecolor=INK, lw=1, zorder=4, label="parada")  # fmt: skip
    kinds = [
        Line2D([], [], color=LISTED["sanctioned"], lw=1.6, label="sancionado o gestor sancionado"),
        Line2D([], [], color=LISTED["watchlisted"], lw=1.6, label="en listas de vigilancia"),
        Line2D([], [], color=INK, marker="s", markerfacecolor="white", ls="", label="parada (1 h o más)"),
    ]
    key = ax.legend(handles=kinds, loc="lower left", bbox_to_anchor=(0, 1.01), ncol=3, frameon=False, fontsize=7.5)
    ax.add_artist(key)
    # Who each number is, under the map in columns: however many, they fit.
    names = [Line2D([], [], ls="", label=label) for label in legend]
    ax.legend(handles=names, loc="upper left", bbox_to_anchor=(0, -0.1), ncol=4, frameon=False, handlelength=0,
              handletextpad=0, columnspacing=2.0, labelcolor=INK, prop={"family": "monospace", "size": 7})  # fmt: skip
    _zones(ax, r)
    _save(fig, path)
    return [v.name or str(v.mmsi) for v in r.listed if v not in shown]


def transits_chart(r: Report, path: Path) -> None:
    """Strait crossings per day, east and west side by side."""
    days = [d for d, _, _ in r.transits_by_day]
    east = np.array([e for _, e, _ in r.transits_by_day])
    west = np.array([w for _, _, w in r.transits_by_day])
    x = np.arange(len(days))
    fig, ax = plt.subplots(figsize=(7, 3), dpi=150)
    ax.bar(x - 0.2, east, 0.38, color=EAST, label="hacia el Mediterráneo")
    ax.bar(x + 0.2, west, 0.38, color=WEST, label="hacia el Atlántico")
    ax.set_xticks(x, [_day(d) for d in days], fontsize=8, color=MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=MUTED)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(EDGE)
    ax.grid(axis="y", color="#ececea", lw=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8)
    ax.set_ylabel("cruces", fontsize=8, color=MUTED)
    _save(fig, path)


def _map(r: Report, extent: tuple[float, float, float, float]) -> tuple[plt.Figure, plt.Axes]:
    west, east, south, north = extent
    aspect = 1 / math.cos(math.radians((south + north) / 2))
    width = 8.0
    height = max(3.0, min(9.0, width * (north - south) * aspect / (east - west)))
    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    ax.set_facecolor(SEA)
    for feature in json.loads(NATURAL_EARTH_LAND.read_text())["features"]:
        geometry = shape(feature["geometry"])
        for polygon in getattr(geometry, "geoms", [geometry]):
            ax.add_patch(Polygon(np.asarray(polygon.exterior.coords), facecolor=LAND, edgecolor=EDGE, lw=0.5, zorder=1))
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect(aspect)
    ax.tick_params(labelsize=7, colors=MUTED)
    for spine in ax.spines.values():
        spine.set_color(EDGE)
    return fig, ax


def _zones(ax: plt.Axes, r: Report) -> None:
    for z in r.recorded:
        lat_min, lon_min, lat_max, lon_max = z.bbox
        analysed = z in r.zones
        ax.add_patch(Rectangle((lon_min, lat_min), lon_max - lon_min, lat_max - lat_min, fill=False, lw=0.8,
                               ls="-" if analysed else "--", edgecolor=MUTED, zorder=2))  # fmt: skip


def _in_zones(point: tuple[float, float], r: Report) -> bool:
    lon, lat = point
    return any(z.bbox[0] <= lat <= z.bbox[2] and z.bbox[1] <= lon <= z.bbox[3] for z in r.zones)


def _extent(points: list[tuple[float, float]], pad: float = 0.05) -> tuple[float, float, float, float]:
    if not points:
        return -6.5, 4.5, 35.2, 42.6
    lon = [p[0] for p in points]
    lat = [p[1] for p in points]
    dx, dy = max(max(lon) - min(lon), 0.2) * pad, max(max(lat) - min(lat), 0.2) * pad
    return min(lon) - dx, max(lon) + dx, min(lat) - dy, max(lat) + dy


def _day(iso: str) -> str:
    months = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    _, month, day = iso.split("-")
    return f"{int(day)} {months[int(month) - 1]}"


def _save(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    # Everything around the axes too: legends above and below a map.
    legends = [a for ax in fig.axes for a in ax.get_children() if isinstance(a, Legend)]
    fig.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.15, bbox_extra_artists=legends)
    plt.close(fig)
