"""Reference data for AIS analysis (sanctions: see sanctions.py).

- Critical infrastructure in Spanish waters: EMODnet Human Activities (telecom cables from Spain's
  CICA, pipelines, platforms, ports) and TeleGeography's submarine cable map (international cables
  and their landing points), CC BY-NC-SA.
- Ports worldwide, to name where vessels stop: the World Port Index (NGA Pub. 150), public domain.
"""

import csv
import io
import itertools
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
import structlog
from shapely import Geometry, box
from shapely.geometry import shape
from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.db.models import Infrastructure, Port

log = structlog.get_logger(component="reference")

EMODNET_WFS = "https://ows.emodnet-humanactivities.eu/wfs"
TELEGEOGRAPHY_CABLES = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"
TELEGEOGRAPHY_LANDINGS = "https://www.submarinecablemap.com/api/v3/landing-point/landing-point-geo.json"
WORLD_PORT_INDEX = "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv"
PORT_NAMES = {"Europa Point": "Gibraltar", "Tangier-Mediterranean": "Tanger Med"}  # the names people use

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


# --- ports -----------------------------------------------------------------------------------------


def world_ports(rows: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Rows of the World Port Index CSV as ports."""
    ports = []
    for row in rows:
        try:
            port_id = int(float(row["World Port Index Number"]))
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
        except KeyError, ValueError:
            continue
        name = row["Main Port Name"].strip()
        ports.append(
            {
                "id": port_id,
                "name": PORT_NAMES.get(name, name),
                "country": row["Country Code"].strip(),
                "locode": row["UN/LOCODE"].strip() or None,
                "size": row["Harbor Size"].strip() or None,
                "lat": lat,
                "lon": lon,
            }
        )
    return ports


async def refresh_ports(engine: AsyncEngine, http: httpx.AsyncClient) -> int:
    """Insert or update the World Port Index's ports (never delete: stops refer to them)."""
    response = await http.get(WORLD_PORT_INDEX, follow_redirects=True)
    response.raise_for_status()
    ports = world_ports(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    async with engine.begin() as conn:
        for batch in itertools.batched(ports, 2000, strict=False):  # within the parameters a statement takes
            stmt = pg_insert(Port).values(batch)
            update = {c: stmt.excluded[c] for c in ("name", "country", "locode", "size", "lat", "lon")}
            await conn.execute(stmt.on_conflict_do_update(index_elements=[Port.id], set_=update))
    log.info("ports_refreshed", ports=len(ports))
    return len(ports)
