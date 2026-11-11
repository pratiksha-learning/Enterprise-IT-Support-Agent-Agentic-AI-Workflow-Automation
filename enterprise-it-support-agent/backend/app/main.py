"""FastAPI application factory.

Run locally:  uvicorn app.main:app --reload --port 8000   (from the backend/ folder)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ALL_ROUTERS
from app.config import Settings, get_settings
from app.container import Container, build_services
from app.db import build_engine, build_session_factory, init_db
from app.errors import register_exception_handlers
from app.observability.logging import configure_logging
from app.observability.middleware import AuthenticationMiddleware, RateLimitMiddleware, RequestContextMiddleware
from app.observability.telemetry import setup_telemetry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    exporter = setup_telemetry(settings)

    app = FastAPI(title=settings.app_name, version="1.0.0",
                  description="Backend for the Teams / Copilot Studio Enterprise IT Support Agent: knowledge "
                              "assistant (RAG), IT ticket management (mock ITSM) and access requests with "
                              "human approval. Local mock mode needs no Microsoft 365 tenant.")
    engine = build_engine(settings.database_url)
    init_db(engine)
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.container = container = Container(settings)

    with app.state.session_factory() as db:
        container.seed_catalog(db)
        if settings.auto_ingest_knowledge and settings.knowledge_source == "local":
            services = build_services(db, container)
            if services.knowledge.repo.count() == 0 or settings.environment == "local":
                result = services.knowledge.ingest(container.knowledge_source())
                db.commit()
                logger.info("knowledge base ingested", extra=result.model_dump())

    register_exception_handlers(app)
    # Starlette runs the LAST added middleware first: CORS -> context -> auth -> rate limit -> routes
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
                       expose_headers=["X-Correlation-ID", "Idempotent-Replayed", "Retry-After"])
    for router in ALL_ROUTERS:
        app.include_router(router)

    try:  # automatic HTTP server spans (optional package)
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*")
    except Exception:  # pragma: no cover
        logger.warning("FastAPI OpenTelemetry instrumentation unavailable")

    logger.info("application started", extra={"auth_mode": settings.auth_mode.value, "telemetry_exporter": exporter,
                                               "environment": settings.environment})
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """Lazily build ``app`` so importing this module (e.g. in tests) has no side effects.
    ``uvicorn app.main:app`` resolves the attribute and triggers creation."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
