"""Shared integration utilities: retry with backoff, fault injection, and errors."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

from app.observability.telemetry import metrics_registry

logger = logging.getLogger(__name__)
T = TypeVar("T")


class IntegrationError(Exception):
    """Raised by adapters. ``transient`` errors are retried."""

    def __init__(self, message: str, *, transient: bool = True, status: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.status = status


class FaultInjector:
    """Lets tests and the local demo simulate downstream outages of mock adapters."""

    COMPONENTS = ("identity", "approval", "provisioning", "notification")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled: set[str] = set()

    def set(self, component: str, enabled: bool) -> None:
        with self._lock:
            (self._enabled.add if enabled else self._enabled.discard)(component)

    def check(self, component: str) -> None:
        if component in self._enabled:
            raise IntegrationError(f"simulated {component} outage", transient=True, status=503)

    def state(self) -> dict[str, bool]:
        return {c: c in self._enabled for c in self.COMPONENTS}

    def clear(self) -> None:
        with self._lock:
            self._enabled.clear()


def with_retry(operation: str, fn: Callable[[], T], *, attempts: int = 3, backoff: float = 0.2,
               sleep: Callable[[float], None] = time.sleep) -> T:
    """Call ``fn`` retrying transient ``IntegrationError`` with exponential backoff."""
    last: IntegrationError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except IntegrationError as exc:
            last = exc
            if not exc.transient or attempt == attempts:
                break
            metrics_registry.inc("retries", operation=operation)
            logger.warning("downstream call failed, retrying",
                           extra={"operation": operation, "attempt": attempt, "error": str(exc)})
            sleep(backoff * (2 ** (attempt - 1)))
    metrics_registry.inc("errors", code=f"DOWNSTREAM_{operation.upper()}")
    assert last is not None
    raise last
