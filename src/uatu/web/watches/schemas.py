from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uatu.web.schemas import OutputSchema


class WatchCreate(BaseModel):
    """A vessel to be told about whenever it is heard: any of IMO, MMSI or name that matches."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    imo: Annotated[int, Field(ge=1_000_000, le=9_999_999)] | None = None
    mmsi: Annotated[int, Field(ge=1, le=999_999_999)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=40)] | None = None  # as broadcast, in any case
    note: Annotated[str, Field(min_length=1, max_length=300)]  # why it is watched

    @model_validator(mode="after")
    def identified(self) -> Self:
        if self.imo is None and self.mmsi is None and self.name is None:
            raise ValueError("an IMO, an MMSI or a name")
        return self


class HeardVessel(OutputSchema):
    mmsi: int
    name: str | None
    last_seen_at: datetime | None


class WatchRead(OutputSchema):
    id: int
    imo: int | None
    mmsi: int | None
    name: str | None
    note: str
    added_at: datetime
    vessels: list[HeardVessel]  # those Uatu has heard that it matches, latest first
