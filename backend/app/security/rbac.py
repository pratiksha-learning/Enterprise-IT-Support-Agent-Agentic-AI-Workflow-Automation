"""Role-based access control: maps roles -> permissions (authorisation only).

Roles correspond to Entra ID *app roles* defined on the backend API app registration
(e.g. ``ITSupport.Employee``); the short names below are what the token validator emits.
"""
from __future__ import annotations

from enum import Enum

from app.security.principal import Principal


class Permission(str, Enum):
    KNOWLEDGE_QUERY = "knowledge:query"
    KNOWLEDGE_INGEST = "knowledge:ingest"
    TICKET_CREATE = "tickets:create"
    TICKET_READ_OWN = "tickets:read_own"
    TICKET_READ_ALL = "tickets:read_all"
    TICKET_UPDATE_STATUS = "tickets:update_status"
    TICKET_CANCEL_OWN = "tickets:cancel_own"
    ACCESS_REQUEST = "access:request"
    ACCESS_READ_OWN = "access:read_own"
    ACCESS_READ_ALL = "access:read_all"
    ACCESS_ADMIN = "access:admin"
    APPROVAL_DECIDE = "approvals:decide"
    APPROVAL_CALLBACK = "approvals:callback"
    AUDIT_READ = "audit:read"
    ADMIN_METRICS = "admin:metrics"
    ADMIN_FAULTS = "admin:faults"
    AGENT_CHAT = "agent:chat"


_EMPLOYEE = {
    Permission.KNOWLEDGE_QUERY, Permission.TICKET_CREATE, Permission.TICKET_READ_OWN,
    Permission.TICKET_CANCEL_OWN, Permission.ACCESS_REQUEST, Permission.ACCESS_READ_OWN, Permission.AGENT_CHAT,
}

ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "employee": _EMPLOYEE,
    "manager": _EMPLOYEE | {Permission.APPROVAL_DECIDE},
    "it_agent": _EMPLOYEE | {Permission.TICKET_READ_ALL, Permission.TICKET_UPDATE_STATUS,
                             Permission.ACCESS_READ_ALL, Permission.AUDIT_READ},
    "admin": _EMPLOYEE | {Permission.TICKET_READ_ALL, Permission.TICKET_UPDATE_STATUS, Permission.ACCESS_READ_ALL,
                          Permission.ACCESS_ADMIN, Permission.AUDIT_READ, Permission.ADMIN_METRICS,
                          Permission.ADMIN_FAULTS, Permission.KNOWLEDGE_INGEST},
    # application role granted only to the Power Automate flow's service principal
    "approvals_callback": {Permission.APPROVAL_CALLBACK},
}

# Entra app-role values -> internal role names
ENTRA_ROLE_MAP = {
    "ITSupport.Employee": "employee",
    "ITSupport.Manager": "manager",
    "ITSupport.Agent": "it_agent",
    "ITSupport.Admin": "admin",
    "ITSupport.ApprovalCallback": "approvals_callback",
}


def permissions_for(principal: Principal) -> set[Permission]:
    perms: set[Permission] = set()
    for role in principal.roles:
        perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


def has_permission(principal: Principal, permission: Permission) -> bool:
    return permission in permissions_for(principal)
