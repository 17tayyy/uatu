"""Reference data for AIS analysis: critical infrastructure in Spanish waters (sanctions: see sanctions.py).

EMODnet Human Activities (telecom cables from Spain's CICA, pipelines, platforms, ports) and
TeleGeography's submarine cable map (international cables and their landing points), CC BY-NC-SA.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import structlog
from shapely import Geometry, box
from shapely.geometry import shape
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.db.models import Infrastructure

log = structlog.get_logger(component="reference")

EMODNET_WFS = "https://ows.emodnet-humanactivities.eu/wfs"
TELEGEOGRAPHY_CABLES = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"
TELEGEOGRAPHY_LANDINGS = "https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json"

# Spanish waters: the Peninsula, the Balearics and the Canaries (lon_min, lat_min, lon_max, lat_max).
SPAIN = box(-19.0, 27.0, 5.0, 44.5)


# --- infrastructure --------------------------------------------------------------------------------

EMODNET_LAYERS = {  # layer -> kind
    "cicacables": "telecom_cable",
    "pipelines": "pipeline",
    "platforms": "platform",
    "portlocations": "port",
}


def emodnet_features(layer: str, collection: dict[str, Any]) -> Iterator[dict[str, Any]]:
    kind = EMODNET_LAYERS[layer]
    for feature in collection.get("features", []):
        if not feature.get("geometry"):
            continue
        p = feature["properties"]
        match layer:
            case "cicacables":  # CICA names them only "operational" or "abandoned" telephone cables
                name = p.get("name") or "telecom cable"
                status = "abandoned" if "ABANDONAD" in name.upper() else "operational"
            case "pipelines":
                name, status = p.get("name") or "pipeline", _status(p.get("status"))
            case "platforms":
                name, status = p.get("name") or "platform", _status(p.get("current_status"))
            case _:
                name, status = p.get("portname") or p.get("port") or "port", None
        yield _row(kind, name, status, f"emodnet:{layer}", shape(feature["geometry"]), p)


def telegeography_features(cables: dict[str, Any], landings: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """International cables, cut to Spanish waters, and their Spanish landing points."""
    for feature in cables.get("features", []):
        geometry = shape(feature["geometry"]).intersection(SPAIN)
        if not geometry.is_empty:
            yield _row("telecom_cable", feature["properties"]["name"], None, "telegeography", geometry, None)
    for feature in landings.get("features", []):
        name = feature["properties"].get("name", "")
        if name.endswith("Spain"):
            yield _row("landing_point", name, None, "telegeography", shape(feature["geometry"]), None)


async def refresh_infrastructure(engine: AsyncEngine, http: httpx.AsyncClient) -> int:
    """Replace the infrastructure table with the current EMODnet and TeleGeography data."""
    rows: list[dict[str, Any]] = []
    lon_min, lat_min, lon_max, lat_max = SPAIN.bounds
    for layer in EMODNET_LAYERS:
        response = await http.get(
            EMODNET_WFS,
            params={
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetFeature",
                "TYPENAMES": f"emodnet:{layer}",
                "OUTPUTFORMAT": "application/json",
                "SRSNAME": "EPSG:4326",
                "BBOX": f"{lon_min},{lat_min},{lon_max},{lat_max},EPSG:4326",  # lon/lat order for GeoServer
            },
        )
        response.raise_for_status()
        rows += emodnet_features(layer, response.json())
    cables = (await http.get(TELEGEOGRAPHY_CABLES)).raise_for_status().json()
    landings = (await http.get(TELEGEOGRAPHY_LANDINGS)).raise_for_status().json()
    rows += telegeography_features(cables, landings)

    async with engine.begin() as conn:
        await conn.execute(delete(Infrastructure))
        if rows:
            await conn.execute(insert(Infrastructure), rows)
    log.info("infrastructure_refreshed", features=len(rows))
    return len(rows)


def _row(kind: str, name: str, status: str | None, source: str, geometry: Geometry, properties: Any) -> dict:
    return {
        "kind": kind,
        "name": name,
        "status": status,
        "source": source,
        "geom": f"SRID=4326;{geometry.wkt}",
        "properties": properties,
    }


def _status(value: object) -> str | None:
    if not value:
        return None
    text = str(value).lower()
    return "operational" if text in ("active", "operational", "in service") else text
