from typing import Annotated

from fastapi import APIRouter, Path, status

from uatu.web.dependencies import CurrentUser, DbSession, SameOrigin
from uatu.web.schemas import DataResponse
from uatu.web.watches import service as watch_service
from uatu.web.watches.schemas import WatchCreate, WatchRead

router = APIRouter(prefix="/watches", tags=["watches"], dependencies=[SameOrigin])


@router.get("")
async def list_watches(db: DbSession) -> DataResponse[list[WatchRead]]:
    """Your watch list, newest first, with the vessels heard that each entry matches."""
    return DataResponse(data=await watch_service.list_watches(db))


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_watch(db: DbSession, _user: CurrentUser, data: WatchCreate) -> DataResponse[WatchRead]:
    """Watch a vessel: an alert whenever it is heard on the coast, once per visit."""
    return DataResponse(data=await watch_service.add_watch(db, data))


@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watch(db: DbSession, _user: CurrentUser, watch_id: Annotated[int, Path(ge=1)]) -> None:
    await watch_service.remove_watch(db, watch_id)
