from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from uatu.web.schemas import OutputSchema


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a password keeps its spaces

    username: Annotated[str, Field(min_length=1, max_length=40)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class SessionRead(OutputSchema):
    username: str | None  # None: not signed in
