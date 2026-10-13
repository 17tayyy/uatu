"""One scene from product to results: ships detected, AIS at acquisition time, pairing and coverage."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.config import BBox
from uatu.sar import report
from uatu.sar.ais import vessels_at_acquisition, vessels_nearby
from uatu.sar.annotation import AcquisitionGeometry, read_geometry
from uatu.sar.detect import CfarParams, Detection
from uatu.sar.match import MatchResult, expected_positions, match, split_by_coverage
from uatu.sar.pipeline import SceneOutput, process
from uatu.sar.raster import Grid


@dataclass(frozen=True, slots=True)
class PairingParams:
    base_tolerance_m: float = 1000.0  # detection-AIS distance tolerated for a vessel with fresh AIS
    drift_fraction: float = 0.25  # of the distance travelled since the nearest AIS report, added on top
    min_ais_nearby: int = 5  # AIS vessels heard around an unpaired detection for it to count as dark
    coverage_radius_m: float = 15_000.0
    coverage_window: timedelta = timedelta(hours=2)
    doppler_sign: float = 1.0


DEFAULT_PAIRING = PairingParams()


@dataclass(frozen=True)
class Analysis:
    name: str  # product name
    geometry: AcquisitionGeometry
    output: SceneOutput
    result: MatchResult | None  # None when AIS was not consulted
    ais_vessels: int

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
) -> Analysis:
    """Detect ships in a scene and, given a database, pair them with the AIS recorded at the time."""
    output = await asyncio.to_thread(process, zip_path, grid, land_path, cfar)
    geometry = await asyncio.to_thread(read_geometry, zip_path)
    name = zip_path.name.removesuffix(".zip")
    if engine is None:
        return Analysis(name, geometry, output, None, 0)

    vessels = await vessels_at_acquisition(engine, geometry, geometry.duration_s, bbox)
    expected = expected_positions(
        vessels, geometry, grid, pairing.base_tolerance_m, pairing.drift_fraction, pairing.doppler_sign
    )
    result = match(output.detections, expected, output.searched, output.coarse)
    middle = geometry.start + timedelta(seconds=geometry.duration_s / 2)
    nearby = await vessels_nearby(
        engine, [(d.lon, d.lat) for d in result.dark], middle, pairing.coverage_radius_m, pairing.coverage_window
    )
    result = split_by_coverage(result, nearby, pairing.min_ais_nearby)
    return Analysis(name, geometry, output, result, len(vessels))


def write_outputs(analysis: Analysis, out_dir: Path) -> None:
    """detections.geojson, ais.geojson (when paired) and quicklook.png."""
    out_dir.mkdir(parents=True, exist_ok=True)
    detections = analysis.output.detections
    report.detections_geojson(detections, analysis.result, out_dir / "detections.geojson")
    if analysis.result is not None:
        report.ais_geojson(analysis.result, out_dir / "ais.geojson")
    title = f"{analysis.name[:3]} {analysis.geometry.start:%Y-%m-%d %H:%M} UTC - {len(detections)} ship detections"
    report.quicklook(analysis.output, analysis.result, title, out_dir / "quicklook.png")
