from fastapi import APIRouter

from uatu.web.dependencies import DbSession
from uatu.web.schemas import DataResponse
from uatu.web.status import service as status_service
from uatu.web.status.schemas import Status

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
async def status(db: DbSession) -> DataResponse[Status]:
    """Whether AIS is being recorded, and a count of what was seen lately."""
    return DataResponse(data=await status_service.status(db))
