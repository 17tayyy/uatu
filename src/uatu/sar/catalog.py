"""Search the Copernicus Data Space Ecosystem (CDSE) catalogue for Sentinel-1 scenes over an area.

Uatu uses IW GRDH products: Interferometric Wide swath (250 km), Ground Range Detected at
high resolution (~20 m, 10 m pixels), dual polarisation VV+VH. Each acquisition is cut into
frames of ~170 km along track; covering the Strait usually takes two consecutive frames of
the same datatake (one satellite pass).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from shapely import Geometry
from shapely.geometry import shape

CATALOGUE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


@dataclass(frozen=True, slots=True)
class Scene:
    """One Sentinel-1 IW GRDH frame in the CDSE catalogue."""

    id: str  # CDSE product UUID
    name: str  # e.g. S1C_IW_GRDH_1SDV_20261009T061853_20261009T061918_009804_013878_91C4_COG.SAFE
    start: datetime
    end: datetime
    orbit_direction: str  # ASCENDING | DESCENDING
    relative_orbit: int
    size_bytes: int
    online: bool
    footprint: Geometry

    @property
    def platform(self) -> str:
        return self.name[:3]

    @property
    def datatake(self) -> str:
        """Identifies the satellite pass: frames of the same pass share it."""
        return self.name.split("_")[7]

    @property
    def frame(self) -> str:
        """Identifies the frame regardless of the processing variant (standard or COG)."""
        return "_".join(self.name.split("_")[:8])

    @property
    def is_cog(self) -> bool:
        # Same data with Cloud Optimised GeoTIFF rasters: ~40% smaller to download.
        return self.name.endswith("_COG.SAFE")

    def coverage(self, aoi: Geometry) -> float:
        """Fraction of the area of interest inside this frame."""
        return self.footprint.intersection(aoi).area / aoi.area


async def search(client: httpx.AsyncClient, aoi: Geometry, start: datetime, end: datetime) -> list[Scene]:
    """IW GRDH frames intersecting the AOI acquired in [start, end), one per frame (COG variant preferred)."""
    query = " and ".join(
        [
            "Collection/Name eq 'SENTINEL-1'",
            "contains(Name,'IW_GRDH')",
            f"OData.CSC.Intersects(area=geography'SRID=4326;{aoi.wkt}')",
            f"ContentDate/Start ge {_odata_time(start)}",
            f"ContentDate/Start lt {_odata_time(end)}",
        ]
    )
    params: dict[str, str] | None = {
        "$filter": query,
        "$orderby": "ContentDate/Start asc",
        "$expand": "Attributes",
        "$top": "1000",
    }
    scenes: list[Scene] = []
    url: str | None = CATALOGUE_URL
    while url is not None:
        response = await client.get(url, params=params)
        response.raise_for_status()
        page = response.json()
        scenes += [_scene(product) for product in page["value"]]
        url, params = page.get("@odata.nextLink"), None  # the next link already carries the query

    by_frame: dict[str, Scene] = {}
    for scene in scenes:
        if scene.frame not in by_frame or scene.is_cog:
            by_frame[scene.frame] = scene
    return sorted(by_frame.values(), key=lambda s: s.start)


def _scene(product: dict[str, Any]) -> Scene:
    attributes = {a["Name"]: a["Value"] for a in product.get("Attributes", [])}
    return Scene(
        id=product["Id"],
        name=product["Name"],
        start=datetime.fromisoformat(product["ContentDate"]["Start"]),
        end=datetime.fromisoformat(product["ContentDate"]["End"]),
        orbit_direction=attributes.get("orbitDirection", ""),
        relative_orbit=int(attributes.get("relativeOrbitNumber", 0)),
        size_bytes=int(product["ContentLength"]),
        online=bool(product.get("Online", True)),
        footprint=shape(product["GeoFootprint"]),
    )


def _odata_time(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def worth_processing(scenes: list[Scene], aoi: Geometry, min_coverage: float) -> list[Scene]:
    """Scenes covering enough of the area: a frame that grazes it costs a 1-2 GB download for little."""
    return [s for s in scenes if s.coverage(aoi) >= min_coverage]


def group_passes(scenes: list[Scene]) -> dict[str, list[Scene]]:
    """Frames grouped by satellite pass (datatake), in acquisition order."""
    passes: dict[str, list[Scene]] = {}
    for scene in sorted(scenes, key=lambda s: s.start):
        passes.setdefault(scene.datatake, []).append(scene)
    return passes
