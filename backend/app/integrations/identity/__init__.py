"""Identity-provider adapters: resolve users and their managers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.integrations.common import FaultInjector, IntegrationError
from app.integrations.graph_client import GraphClient
from app.security.auth import MockDirectory


@dataclass(frozen=True)
class DirectoryUser:
    id: str
    upn: str
    display_name: str
    enabled: bool = True


class IdentityProvider(Protocol):
    name: str

    def get_user(self, user_id: str) -> DirectoryUser | None: ...
    def get_manager(self, user_id: str) -> DirectoryUser | None: ...


class MockIdentityProvider:
    """Reads the fictional directory in ``app/data/mock_directory.json``."""
    name = "mock"

    def __init__(self, directory: MockDirectory, faults: FaultInjector):
        self._dir = directory
        self._faults = faults

    def _to_user(self, raw: dict | None) -> DirectoryUser | None:
        if not raw:
            return None
        return DirectoryUser(raw["id"], raw["upn"], raw["display_name"], raw.get("enabled", True))

    def get_user(self, user_id: str) -> DirectoryUser | None:
        self._faults.check("identity")
        return self._to_user(self._dir.get(user_id))

    def get_manager(self, user_id: str) -> DirectoryUser | None:
        self._faults.check("identity")
        user = self._dir.get(user_id)
        if not user or not user.get("manager_id"):
            return None
        return self._to_user(self._dir.get(user["manager_id"]))


class GraphIdentityProvider:
    """Microsoft Graph: ``GET /users/{id}`` and ``GET /users/{id}/manager``.

    App permission required: ``User.Read.All`` (application) - admin consent.
    """
    name = "graph"

    def __init__(self, client: GraphClient):
        self._graph = client

    @staticmethod
    def _map(body: dict) -> DirectoryUser:
        return DirectoryUser(body["id"], body.get("userPrincipalName", ""), body.get("displayName", ""),
                             body.get("accountEnabled", True))

    def get_user(self, user_id: str) -> DirectoryUser | None:
        try:
            return self._map(self._graph.get_json(
                f"/users/{user_id}", params={"$select": "id,userPrincipalName,displayName,accountEnabled"}))
        except IntegrationError as exc:
            if exc.status == 404:
                return None
            raise

    def get_manager(self, user_id: str) -> DirectoryUser | None:
        try:
            return self._map(self._graph.get_json(
                f"/users/{user_id}/manager", params={"$select": "id,userPrincipalName,displayName,accountEnabled"}))
        except IntegrationError as exc:
            if exc.status == 404:   # no manager assigned
                return None
            raise
