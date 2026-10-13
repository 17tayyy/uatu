"""Acquisition geometry and timing from a GRD product's annotation.

A frame is acquired line by line over ~25 s, so each place in it was seen at a slightly
different time, from a different slant range and incidence angle. The annotation gives
these on a grid of tie points, interpolated here to any lon/lat.
"""

import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

SPEED_OF_LIGHT = 299_792_458.0


@dataclass(frozen=True)
class AcquisitionGeometry:
    start: datetime
    heading_deg: float  # satellite ground track, clockwise from north
    satellite_speed: float  # m/s
    lonlat: np.ndarray  # (n, 2) tie points
    seconds: np.ndarray  # (n,) azimuth time after `start`
    slant_range: np.ndarray  # (n,) metres
    incidence_deg: np.ndarray  # (n,)

    @property
    def duration_s(self) -> float:
        return float(self.seconds.max())

    @property
    def look_deg(self) -> float:
        """Direction the radar looks along the ground: Sentinel-1 looks right of its track."""
        return (self.heading_deg + 90) % 360

    def at(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Seconds after start, slant range and incidence angle where the radar saw each point."""
        values = np.column_stack([self.seconds, self.slant_range, self.incidence_deg])
        points = np.column_stack([lon, lat])
        result = LinearNDInterpolator(self.lonlat, values)(points)
        outside = np.isnan(result[:, 0])
        if outside.any():  # just beyond the outermost tie points
            result[outside] = NearestNDInterpolator(self.lonlat, values)(points[outside])
        return result[:, 0], result[:, 1], result[:, 2]

    def time_at(self, lon: np.ndarray, lat: np.ndarray) -> list[datetime]:
        seconds, _, _ = self.at(lon, lat)
        return [self.start + timedelta(seconds=float(s)) for s in seconds]


def read_geometry(zip_path: Path, polarisation: str = "VV") -> AcquisitionGeometry:
    pol = f"-{polarisation.lower()}-"
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if "/annotation/s1" in n and pol in n and n.endswith(".xml"))
        root = ElementTree.fromstring(z.read(name))

    start = _utc(root.findtext("adsHeader/startTime"))
    velocities = [
        [float(v.findtext(axis)) for axis in "xyz"] for v in root.iterfind("generalAnnotation/orbitList/orbit/velocity")
    ]
    points = root.findall("geolocationGrid/geolocationGridPointList/geolocationGridPoint")
    return AcquisitionGeometry(
        start=start,
        heading_deg=float(root.findtext("generalAnnotation/productInformation/platformHeading")) % 360,
        satellite_speed=float(np.linalg.norm(velocities, axis=1).mean()),
        lonlat=np.array([(float(p.findtext("longitude")), float(p.findtext("latitude"))) for p in points]),
        seconds=np.array([(_utc(p.findtext("azimuthTime")) - start).total_seconds() for p in points]),
        slant_range=np.array([float(p.findtext("slantRangeTime")) * SPEED_OF_LIGHT / 2 for p in points]),
        incidence_deg=np.array([float(p.findtext("incidenceAngle")) for p in points]),
    )


def _utc(text: str | None) -> datetime:
    return datetime.fromisoformat(text or "").replace(tzinfo=UTC)
