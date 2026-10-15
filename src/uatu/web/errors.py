"""Domain errors and the one error format every response uses: {"error": {"code", "message", "request_id"}}."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(component="web")


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"
    default_message: str = "Bad request"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(f"{resource} not found")


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    default_message = "Sign in first"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many attempts: wait a few minutes"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "Forbidden"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Invalid input"},
    500: {"model": ErrorResponse, "description": "Internal error"},
}


def error_response(status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details, request_id=request_id))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]  # never echo the input
        return error_response(422, "invalid_input", "Invalid input", {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("http.unhandled_error")
        return error_response(500, "internal_error", "Internal error")
