"""Notification adapters. The database copy is always written by the service; adapters deliver."""
from __future__ import annotations

from typing import Protocol

import httpx

from app.integrations.common import FaultInjector, IntegrationError


class NotificationProvider(Protocol):
    name: str

    def send(self, recipient_upn: str, subject: str, body: str) -> None: ...


class MockNotificationProvider:
    """Local inbox only (visible in the demo UI under 'Notifications')."""
    name = "mock"

    def __init__(self, faults: FaultInjector):
        self._faults = faults

    def send(self, recipient_upn: str, subject: str, body: str) -> None:
        self._faults.check("notification")


class TeamsFlowNotificationProvider:
    """Posts to a Power Automate HTTP-trigger flow which sends a Teams chat message
    ('Post message in a chat or channel' as the Flow bot) to the recipient."""
    name = "teams_webhook"

    def __init__(self, flow_url: str, transport: httpx.BaseTransport | None = None):
        self._url = flow_url
        self._http = httpx.Client(transport=transport, timeout=10.0)

    def send(self, recipient_upn: str, subject: str, body: str) -> None:
        try:
            resp = self._http.post(self._url, json={"recipient": recipient_upn, "subject": subject, "body": body})
        except httpx.TransportError as exc:
            raise IntegrationError("notification flow unreachable") from exc
        if resp.status_code >= 400:
            raise IntegrationError(f"notification flow returned {resp.status_code}",
                                   transient=resp.status_code >= 500, status=resp.status_code)
