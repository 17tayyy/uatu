from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uatu.web.schemas import OutputSchema


class Verdict(StrEnum):
    """What a radar detection turned out to be, by your judgement."""

    VESSEL_WITHOUT_AIS = "vessel_without_ais"  # a vessel, and it was not transmitting
    VESSEL_WITH_AIS = "vessel_with_ais"  # a vessel transmitting, missed by the pairing
    RECEPTION_GAP = "reception_gap"  # a vessel transmitting that aisstream did not hear
    NOT_A_VESSEL = "not_a_vessel"  # sea clutter, a structure, an artefact


class ReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    mmsi: Annotated[int, Field(ge=1, le=999_999_999)] | None = None  # which vessel it is, when known
    note: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def a_vessel_to_name(self) -> Self:
        if self.mmsi is not None and self.verdict == Verdict.NOT_A_VESSEL:
            raise ValueError("not a vessel: no MMSI")
        return self


class ReviewRead(OutputSchema):
    verdict: Verdict
    mmsi: int | None
    vessel_name: str | None
    note: str | None
    reviewed_by: str | None  # the user, while it exists
    reviewed_at: datetime
