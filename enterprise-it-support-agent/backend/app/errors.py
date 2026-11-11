"""Structured application errors mapped to HTTP status codes.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "correlation_id": "...", "details": [...]}}
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.context import get_correlation_id
from app.observability.telemetry import metrics_registry

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, details: list | None = None,
                 status_code: int | None = None, headers: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or []
        self.headers = headers or {}


class AuthenticationError(AppError):
    status_code, code = 401, "UNAUTHENTICATED"


class AuthorizationError(AppError):
    status_code, code = 403, "FORBIDDEN"


class NotFoundError(AppError):
    status_code, code = 404, "NOT_FOUND"


class ConflictError(AppError):
    status_code, code = 409, "CONFLICT"


class ValidationFailed(AppError):
    status_code, code = 422, "VALIDATION_FAILED"


class RateLimitedError(AppError):
    status_code, code = 429, "RATE_LIMITED"


class DownstreamError(AppError):
    """A dependency (identity provider, approval flow, provisioning, Graph...) failed."""
    status_code, code = 502, "DOWNSTREAM_UNAVAILABLE"


def error_body(code: str, message: str, details: list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "correlation_id": get_correlation_id(),
                      "details": details or []}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        metrics_registry.inc("errors", code=exc.code)
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        logger.log(level, "request failed", extra={"error_code": exc.code, "status": exc.status_code})
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        metrics_registry.inc("errors", code="VALIDATION_FAILED")
        details = [{"field": ".".join(str(p) for p in e.get("loc", []) if p != "body"),
                    "message": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(error_body("VALIDATION_FAILED", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        metrics_registry.inc("errors", code="INTERNAL_ERROR")
        logger.exception("unhandled error")
        # never leak internals to the caller
        return JSONResponse(error_body("INTERNAL_ERROR", "An unexpected error occurred"), status_code=500)
