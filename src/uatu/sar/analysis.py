"""One scene from product to results: ships detected, AIS at acquisition time, pairing and coverage, wakes."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.config import BBox
from uatu.sar import report
from uatu.sar.ais import AisVessel, coverage_around, silent_vessels, vessels_at_acquisition
from uatu.sar.annotation import AcquisitionGeometry, read_geometry
from uatu.sar.detect import CfarParams, Detection
from uatu.sar.match import MatchResult, expected_positions, match, split_by_coverage
from uatu.sar.pipeline import SceneOutput, process
from uatu.sar.raster import Grid
from uatu.sar.wake import Wake, find_wakes


@dataclass(frozen=True, slots=True)
class PairingParams:
    base_tolerance_m: float = 1000.0  # detection-AIS distance tolerated for a vessel with fresh AIS
    drift_fraction: float = 0.25  # of the distance travelled since the nearest AIS report, added on top
    # An unpaired detection is dark only if AIS was being received right there: this many other
    # vessels heard within coverage_radius_m and ±coverage_window, each heard this many times on
    # average. Measured locally because coverage has holes at the scale of a bay: aisstream.io
    # hears vessels in the strait's lanes 10-20 times in 4 h, and those in the anchorages behind
    # the Rock of Gibraltar about once.
    min_ais_nearby: int = 3
    min_reports_per_vessel: float = 3.0
    coverage_radius_m: float = 3_000.0
    coverage_window: timedelta = timedelta(hours=2)
    doppler_sign: float = 1.0
    # Linking unpaired detections to vessels that had gone silent: beyond this uncertainty on where
    # the vessel would be, any link is a guess.
    max_suspect_tolerance_m: float = 8_000.0
    # Paired vessels slower than this (AIS) leave no wake to look for.
    wake_min_sog_kn: float = 1.0


DEFAULT_PAIRING = PairingParams()


@dataclass(frozen=True, slots=True)
class Suspect:
    """A vessel that had stopped transmitting and would be about where an unpaired detection is."""

    vessel: AisVessel
    distance_m: float


@dataclass(frozen=True)
class Analysis:
    name: str  # product name
    geometry: AcquisitionGeometry
    output: SceneOutput
    result: MatchResult | None  # None when AIS was not consulted
    ais_vessels: int
    suspects: dict[Detection, Suspect] = field(default_factory=dict)
    image: Path | None = None  # the backscatter at 20 m on the scene's grid, when asked for
    wakes: dict[Detection, Wake] = field(default_factory=dict)  # when asked for: the best one of each, however faint

    def seen_at(self, detections: list[Detection]) -> list[datetime]:
        """When the radar looked at each detection's spot."""
        if not detections:
            return []
        return self.geometry.time_at(np.array([d.lon for d in detections]), np.array([d.lat for d in detections]))


async def analyse(
    zip_path: Path,
    grid: Grid,
    land_path: Path,
    cfar: CfarParams,
    engine: AsyncEngine | None,
    bbox: BBox,
    pairing: PairingParams = DEFAULT_PAIRING,
    image_path: Path | None = None,
    wakes: bool = False,
) -> Analysis:
    """Detect ships in a scene and, given a database, pair them with the AIS recorded at the time.

    image_path: also keep the backscatter at 20 m there, for write_outputs to turn into map tiles.
    wakes: also look for the wake of each detection (see uatu.sar.wake), but those of vessels AIS
    says were still; a few seconds each.
    """
    output = await asyncio.to_thread(process, zip_path, grid, land_path, cfar, image_path=image_path)
    geometry = await asyncio.to_thread(read_geometry, zip_path)
    name = zip_path.name.removesuffix(".zip")
    if engine is None:
        found = await asyncio.to_thread(find_wakes, zip_path, output.detections, geometry, grid) if wakes else {}
        return Analysis(name, geometry, output, None, 0, image=image_path, wakes=found)

    vessels = await vessels_at_acquisition(engine, geometry, geometry.duration_s, bbox)
    expected = expected_positions(
        vessels, geometry, grid, pairing.base_tolerance_m, pairing.drift_fraction, pairing.doppler_sign
    )
    result = match(output.detections, expected, output.searched, output.coarse)
    middle = geometry.start + timedelta(seconds=geometry.duration_s / 2)
    nearby = await coverage_around(
        engine, [(d.lon, d.lat) for d in result.dark], middle, pairing.coverage_radius_m, pairing.coverage_window
    )
    result = split_by_coverage(result, nearby, pairing.min_ais_nearby, pairing.min_reports_per_vessel)
    suspects = await find_suspects(engine, result, output, geometry, grid, bbox, pairing)
    found = {}
    if wakes:
        still = {
            m.detection
            for m in result.matches
            if m.expected.vessel.sog is not None and m.expected.vessel.sog < pairing.wake_min_sog_kn
        }
        found = await asyncio.to_thread(
            find_wakes, zip_path, output.detections, geometry, grid, still, doppler_sign=pairing.doppler_sign
        )
    return Analysis(name, geometry, output, result, len(vessels), suspects, image=image_path, wakes=found)


async def find_suspects(
    engine: AsyncEngine,
    result: MatchResult,
    output: SceneOutput,
    geometry: AcquisitionGeometry,
    grid: Grid,
    bbox: BBox,
    pairing: PairingParams,
) -> dict[Detection, Suspect]:
    """Pair unpaired detections with vessels that were silent at acquisition time, the same way
    detections are paired with live AIS, but with the uncertainty of how long they had been silent."""
    unpaired = [*result.dark, *result.no_coverage]
    if not unpaired:
        return {}
    silent = await silent_vessels(engine, geometry, geometry.duration_s, bbox)
    ghosts = [
        e
        for e in expected_positions(
            silent, geometry, grid, pairing.base_tolerance_m, pairing.drift_fraction, pairing.doppler_sign
        )
        if e.tolerance_m <= pairing.max_suspect_tolerance_m
    ]
    linked = match(unpaired, ghosts, output.searched, output.coarse)
    return {m.detection: Suspect(m.expected.vessel, m.distance_m) for m in linked.matches}


def write_outputs(analysis: Analysis, out_dir: Path) -> None:
    """detections.geojson, ais.geojson (when paired), quicklook.png, the map overlay (backscatter.png +
    backscatter.json) and, when the analysis kept the backscatter at 20 m, radar.tif for map tiles."""
    out_dir.mkdir(parents=True, exist_ok=True)
    detections = analysis.output.detections
    report.detections_geojson(detections, analysis.result, out_dir / "detections.geojson", analysis.wakes)
    if analysis.result is not None:
        report.ais_geojson(analysis.result, out_dir / "ais.geojson")
    title = f"{analysis.name[:3]} {analysis.geometry.start:%Y-%m-%d %H:%M} UTC - {len(detections)} ship detections"
    report.quicklook(analysis.output, analysis.result, title, out_dir / "quicklook.png")
    report.backscatter_overlay(analysis.output, out_dir / "backscatter.png", out_dir / "backscatter.json")
    if analysis.image is not None and analysis.image.exists():
        report.radar_image(analysis.image, out_dir / "radar.tif")
        analysis.image.unlink()
