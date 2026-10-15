from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.web.basemap.schemas import LandProperties
from uatu.web.config import WebSettings
from uatu.web.errors import ForbiddenError, NotFoundError
from uatu.web.schemas import FeatureCollection
from uatu.web.scope import ALL, Scope, scope


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_settings(request: Request) -> WebSettings:
    return request.app.state.settings


async def get_scope(
    request: Request, zone: Annotated[str, Query(pattern=r"^[a-z]{3,16}$", description="a zone's key, or todas")] = ALL
) -> Scope:
    found = scope(zone, request.app.state.settings.zones)
    if found is None:
        raise NotFoundError("Zone")
    return found


async def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


async def get_land(request: Request) -> FeatureCollection[LandProperties]:
    return request.app.state.land


async def same_origin(request: Request) -> None:
    """Writes only from the dashboard's own page. It has no login (local network only): this keeps any other
    site open in the same browser from changing things through it. Browsers send Origin on every write."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if origin is None or urlsplit(origin).netloc != request.headers.get("host"):
        raise ForbiddenError("Writes only from the dashboard itself")


DbSession = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[WebSettings, Depends(get_settings)]
ZoneScope = Annotated[Scope, Depends(get_scope)]
HttpClient = Annotated[httpx.AsyncClient, Depends(get_http)]
LandOutlines = Annotated[FeatureCollection[LandProperties], Depends(get_land)]
SameOrigin = Depends(same_origin)  # for a router's dependencies
