"""Minimal Microsoft Graph client (client-credentials flow) used by the real adapters.

Requires a tenant app registration. Not exercised against a live tenant in this repo;
unit tests use ``httpx.MockTransport`` to verify request shapes, token caching and
retry/throttling behaviour.
"""
from __future__ import annotations

import threading
import time

import httpx

from app.integrations.common import IntegrationError

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        self._token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(transport=transport, timeout=timeout)
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            resp = self._http.post(self._token_url, data={
                "client_id": self._client_id, "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
            if resp.status_code != 200:
                raise IntegrationError("Graph token acquisition failed", transient=resp.status_code >= 500,
                                       status=resp.status_code)
            body = resp.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
            return self._token

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._get_token()}"
        try:
            resp = self._http.request(method, url, headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise IntegrationError(f"Graph transport error: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise IntegrationError(f"Graph {resp.status_code}", transient=True, status=resp.status_code)
        if resp.status_code >= 400:
            raise IntegrationError(f"Graph {resp.status_code}", transient=False, status=resp.status_code)
        return resp

    def get_json(self, path: str, **kwargs) -> dict:
        return self.request("GET", path, **kwargs).json()
