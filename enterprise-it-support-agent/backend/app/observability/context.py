"""Request-scoped context (correlation ID, principal) shared by logging and audit."""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")
principal_id_var: ContextVar[str] = ContextVar("principal_id", default="-")

_VALID_CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(value: str | None) -> str:
    """Accept an inbound correlation id only if it is well-formed (prevents log injection)."""
    if value and _VALID_CORRELATION.match(value):
        return value
    return new_correlation_id()


def get_correlation_id() -> str:
    return correlation_id_var.get()
