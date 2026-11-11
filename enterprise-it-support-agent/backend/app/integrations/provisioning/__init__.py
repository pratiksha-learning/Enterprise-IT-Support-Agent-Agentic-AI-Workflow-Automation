"""Least-privilege access provisioning adapters.

Each (application, role) maps to exactly one security group. Adapters can only add/remove
the requesting user to/from that group - they cannot create groups, assign directory roles,
or touch any other principal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.integrations.common import FaultInjector, IntegrationError
from app.integrations.graph_client import GRAPH_BASE, GraphClient


@dataclass
class ProvisioningResult:
    reference: str
    mode: str          # mock | entra
    simulated: bool


class ProvisioningProvider(Protocol):
    name: str

    def grant(self, user_id: str, group_id: str) -> ProvisioningResult: ...
    def revoke(self, user_id: str, group_id: str) -> None: ...


class MockProvisioningProvider:
    """Simulates a grant. NEVER calls any external system - safe for local demos."""
    name = "mock"

    def __init__(self, faults: FaultInjector):
        self._faults = faults

    def grant(self, user_id: str, group_id: str) -> ProvisioningResult:
        self._faults.check("provisioning")
        return ProvisioningResult(reference=f"mock-grant:{group_id}:{user_id}", mode="mock", simulated=True)

    def revoke(self, user_id: str, group_id: str) -> None:
        self._faults.check("provisioning")


class EntraGroupProvisioningProvider:
    """Adds the user to the role's Entra security group via Microsoft Graph.

    Least-privilege setup: grant the provisioning app ``GroupMember.ReadWrite.All`` and make
    the app an *owner* only of the requestable groups (or use a role-assignable administrative
    unit), so it cannot modify other groups. Prefer Entra ID Governance access packages for
    production lifecycle management. Only enabled when ALLOW_REAL_PROVISIONING=true.
    """
    name = "entra_groups"

    def __init__(self, client: GraphClient, allowed_group_ids: set[str]):
        self._graph = client
        self._allowed = allowed_group_ids

    def _check(self, group_id: str) -> None:
        if group_id not in self._allowed:
            raise IntegrationError("group is not in the requestable catalog", transient=False, status=403)

    def grant(self, user_id: str, group_id: str) -> ProvisioningResult:
        self._check(group_id)
        try:
            self._graph.request("POST", f"/groups/{group_id}/members/$ref",
                                json={"@odata.id": f"{GRAPH_BASE}/directoryObjects/{user_id}"})
        except IntegrationError as exc:
            # Graph returns 400 when the member already exists - treat as idempotent success
            if exc.status != 400:
                raise
        return ProvisioningResult(reference=f"entra-group:{group_id}:{user_id}", mode="entra", simulated=False)

    def revoke(self, user_id: str, group_id: str) -> None:
        self._check(group_id)
        try:
            self._graph.request("DELETE", f"/groups/{group_id}/members/{user_id}/$ref")
        except IntegrationError as exc:
            if exc.status != 404:
                raise
