"""Pydantic request/response schemas (API contract).

Request models use ``extra="forbid"`` so that callers (including an LLM-driven agent)
cannot smuggle fields such as ``requester_id``, ``status`` or ``approved`` into a request.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _ser_dt(self, v: Any):
        if isinstance(v, datetime):
            aware = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return v


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text: str) -> str:
    return _CONTROL.sub("", text)


# --------------------------------------------------------------------------- auth
class DevTokenRequest(StrictModel):
    user_id: str = Field(pattern=r"^u-[a-z]{2,20}$", examples=["u-alice"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: "PrincipalOut"


class PrincipalOut(BaseModel):
    id: str
    upn: str
    display_name: str
    roles: list[str]
    groups: list[str]
    auth_type: str


class DirectoryUserOut(BaseModel):
    id: str
    display_name: str
    upn: str
    roles: list[str]
    title: str = ""


# --------------------------------------------------------------------------- tickets
class TicketCategory(str, Enum):
    hardware = "hardware"
    software = "software"
    network = "network"
    access = "access"
    account = "account"
    other = "other"


class TicketPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class TicketStatus(str, Enum):
    new = "new"
    in_progress = "in_progress"
    on_hold = "on_hold"
    resolved = "resolved"
    closed = "closed"
    cancelled = "cancelled"


class TicketCreate(StrictModel):
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=4000)
    category: TicketCategory = TicketCategory.other
    priority: TicketPriority = TicketPriority.P3

    @field_validator("title", "description")
    @classmethod
    def _no_control(cls, v: str) -> str:
        return _clean(v)


class TicketStatusUpdate(StrictModel):
    status: TicketStatus
    comment: str | None = Field(default=None, max_length=2000)


class TicketEventOut(OutModel):
    from_status: str | None
    to_status: str
    actor_id: str
    comment: str | None
    created_at: datetime


class TicketOut(OutModel):
    ticket_number: str
    title: str
    description: str
    category: str
    priority: str
    status: str
    requester_id: str
    requester_upn: str
    assigned_to: str | None
    source: str
    related_access_request_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TicketStatusOut(OutModel):
    ticket_number: str
    status: str
    priority: str
    updated_at: datetime


class TicketList(BaseModel):
    items: list[TicketOut]
    total: int


# --------------------------------------------------------------------------- access
class AccessRequestCreate(StrictModel):
    application_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    role: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    justification: str = Field(min_length=20, max_length=1000)
    duration_days: int = Field(ge=1, le=365)

    @field_validator("justification")
    @classmethod
    def _clean_just(cls, v: str) -> str:
        return _clean(v)


class ApprovalOut(OutModel):
    id: str
    stage: int
    approver_id: str
    approver_upn: str
    approver_reason: str
    status: str
    provider: str
    comment: str | None
    decided_by_id: str | None
    decision_channel: str | None
    created_at: datetime
    due_at: datetime
    decided_at: datetime | None


class AccessRequestOut(OutModel):
    id: str
    request_number: str
    requester_id: str
    requester_upn: str
    application_id: str
    role: str
    justification: str
    duration_days: int
    status: str
    privileged: bool
    required_stages: int
    current_stage: int
    failure_reason: str | None
    provisioning_reference: str | None
    access_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    approvals: list[ApprovalOut] = []


class AccessRequestList(BaseModel):
    items: list[AccessRequestOut]
    total: int


class ApplicationRoleOut(BaseModel):
    name: str
    description: str
    level: int
    privileged: bool
    max_duration_days: int


class ApplicationOut(BaseModel):
    id: str
    name: str
    description: str
    roles: list[ApplicationRoleOut]
    max_duration_days: int


class ApprovalDecisionValue(str, Enum):
    approve = "approve"
    reject = "reject"


class ApprovalDecision(StrictModel):
    decision: ApprovalDecisionValue
    comment: str | None = Field(default=None, max_length=1000)


class ApprovalCallback(StrictModel):
    """Payload posted back by the Power Automate approval flow (or the local mock)."""
    approval_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: ApprovalDecisionValue
    responder_id: str = Field(min_length=2, max_length=64)
    responder_upn: str = Field(min_length=3, max_length=256)
    comment: str | None = Field(default=None, max_length=1000)


class PendingApprovalOut(BaseModel):
    approval: ApprovalOut
    request: AccessRequestOut


# --------------------------------------------------------------------------- knowledge
class KnowledgeQuery(StrictModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=10)


class Citation(BaseModel):
    index: int
    document_id: str
    title: str
    source_uri: str
    heading: str
    score: float
    snippet: str


class KnowledgeAnswer(BaseModel):
    answer: str
    grounded: bool
    fallback: bool
    citations: list[Citation]
    blocked_content_count: int = 0
    provider: str


class KnowledgeDocumentOut(OutModel):
    id: str
    source: str
    source_uri: str
    title: str
    allowed_groups: list[str]
    sensitivity: str
    injection_flags: int
    ingested_at: datetime


class IngestResult(BaseModel):
    documents_ingested: int
    documents_skipped: int
    chunks_created: int
    flagged_documents: list[str]


# --------------------------------------------------------------------------- audit / notifications
class AuditEntryOut(OutModel):
    id: int
    timestamp: datetime
    correlation_id: str
    actor_id: str
    actor_type: str
    action: str
    target_type: str
    target_id: str
    outcome: str
    details: dict
    hash: str


class AuditList(BaseModel):
    items: list[AuditEntryOut]
    total: int


class AuditVerifyResult(BaseModel):
    valid: bool
    entries_checked: int
    first_invalid_id: int | None = None


class NotificationOut(OutModel):
    id: str
    channel: str
    subject: str
    body: str
    status: str
    related_id: str | None
    created_at: datetime


# --------------------------------------------------------------------------- agent
class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ToolCallOut(BaseModel):
    tool: str
    status: str
    latency_ms: float


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    intent: str
    tool_calls: list[ToolCallOut] = []
    citations: list[Citation] = []
    data: dict | None = None
    suggested_replies: list[str] = []


class FaultInjectionRequest(StrictModel):
    component: str = Field(pattern=r"^(identity|approval|provisioning|notification)$")
    enabled: bool


TokenResponse.model_rebuild()
