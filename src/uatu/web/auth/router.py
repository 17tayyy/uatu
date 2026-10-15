from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status

from uatu.web.auth import service as auth_service
from uatu.web.auth.schemas import LoginRequest, SessionRead
from uatu.web.dependencies import SESSION_COOKIE, DbSession, LoginThrottle, SameOrigin, Settings
from uatu.web.schemas import DataResponse

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[SameOrigin])
SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]


@router.post("/login", dependencies=[LoginThrottle])
async def login(data: LoginRequest, db: DbSession, settings: Settings, response: Response) -> DataResponse[SessionRead]:
    """Sign in: a session cookie for this browser, valid 30 days."""
    user, token, expires = await auth_service.sign_in(db, data.username, data.password)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        expires=expires,
        path="/api/",
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return DataResponse(data=SessionRead(username=user.username))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(db: DbSession, response: Response, token: SessionCookie = None) -> None:
    await auth_service.sign_out(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/api/")


@router.get("/session")
async def session(db: DbSession, token: SessionCookie = None) -> DataResponse[SessionRead]:
    """Who is signed in on this browser, if anyone."""
    user = await auth_service.user_for(db, token) if token else None
    return DataResponse(data=SessionRead(username=user.username if user else None))
