from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.web.basemap.schemas import LandProperties
from uatu.web.config import WebSettings
from uatu.web.schemas import FeatureCollection


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_settings(request: Request) -> WebSettings:
    return request.app.state.settings


async def get_land(request: Request) -> FeatureCollection[LandProperties]:
    return request.app.state.land


DbSession = Annotated[AsyncSession, Depends(get_db)]
Settings = Annotated[WebSettings, Depends(get_settings)]
LandOutlines = Annotated[FeatureCollection[LandProperties], Depends(get_land)]
