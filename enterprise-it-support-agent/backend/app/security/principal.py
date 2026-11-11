"""Authenticated principal (who is calling) - produced by authentication, consumed by authorisation."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    id: str                 # Entra object id (oid) or mock id
    upn: str
    display_name: str
    roles: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    auth_type: str = "user"  # user | service
    app_id: str | None = None  # client application id (azp/appid) for service principals

    def has_role(self, role: str) -> bool:
        return role in self.roles
