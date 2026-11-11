"""HTTP middleware: correlation IDs + latency metrics, authentication, rate limiting."""
from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.errors import AppError, error_body
from app.observability.context import correlation_id_var, principal_id_var, sanitize_correlation_id
from app.observability.telemetry import metrics_registry

logger = logging.getLogger("http")
_EXEMPT = ("/health", "/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = sanitize_correlation_id(request.headers.get("x-correlation-id"))
        token = correlation_id_var.set(cid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            metrics_registry.inc("http_requests", route=f"{request.method} {path}", status=str(status))
            metrics_registry.observe("http_latency_ms", elapsed, route=f"{request.method} {path}")
            if not request.url.path.startswith("/health"):
                principal = getattr(request.state, "principal", None)
                logger.info("request completed", extra={"method": request.method, "route": path, "status": status,
                                                        "duration_ms": round(elapsed, 2),
                                                        "caller_id": principal.id if principal else "anonymous"})
            correlation_id_var.reset(token)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Authentication only: validates the bearer token and attaches ``request.state.principal``.
    Authorisation is enforced per-endpoint by RBAC dependencies."""

    async def dispatch(self, request: Request, call_next):
        request.state.principal = None
        request.state.auth_error = None
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            container = request.app.state.container
            validator = container.mock_tokens or container.entra_validator
            try:
                request.state.principal = validator.validate(header[7:].strip())
                principal_id_var.set(request.state.principal.id)
            except AppError as exc:
                request.state.auth_error = exc
        elif header:
            request.state.auth_error = AppError("Unsupported authorization scheme", code="INVALID_TOKEN",
                                                status_code=401)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        container = request.app.state.container
        if not container.settings.rate_limit_enabled or request.url.path.startswith(_EXEMPT) \
                or request.method == "OPTIONS":
            return await call_next(request)
        principal = getattr(request.state, "principal", None)
        key = f"user:{principal.id}" if principal else f"ip:{request.client.host if request.client else 'unknown'}"
        # state-changing calls cost more than reads
        cost = 1.0 if request.method in ("GET", "HEAD") else 2.0
        allowed, retry_after = container.rate_limiter.allow(key, cost)
        if not allowed:
            metrics_registry.inc("errors", code="RATE_LIMITED")
            return JSONResponse(error_body("RATE_LIMITED", "Too many requests - slow down"), status_code=429,
                                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))})
        return await call_next(request)
