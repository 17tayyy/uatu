from typing import Annotated

from fastapi import APIRouter, Path

from uatu.web.companies import service as company_service
from uatu.web.companies.schemas import CompanyDetail
from uatu.web.dependencies import DbSession
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/{company_id}")
async def get_company(
    db: DbSession,
    company_id: Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")],
) -> DataResponse[CompanyDetail]:
    """A company behind vessels (an OpenSanctions entity id): what the sources say it owns and manages,
    and which of those vessels Uatu has heard."""
    return DataResponse(data=await company_service.get_company(db, company_id))
