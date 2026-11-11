"""SQLAlchemy ORM models (persistence layer)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalise everything to aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- tickets
class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    requester_id: Mapped[str] = mapped_column(String(64), index=True)
    requester_upn: Mapped[str] = mapped_column(String(256))
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="api")
    related_access_request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    events: Mapped[list["TicketEvent"]] = relationship(back_populates="ticket", order_by="TicketEvent.id",
                                                        cascade="all, delete-orphan")


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ticket: Mapped[Ticket] = relationship(back_populates="events")


# --------------------------------------------------------------------------- access
class Application(Base):
    """Catalog of applications employees may request access to."""
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(String(64))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    roles: Mapped[list] = mapped_column(JSON, default=list)  # [{name, description, level, privileged, group_id, max_duration_days}]
    max_duration_days: Mapped[int] = mapped_column(Integer, default=90)


class AccessRequest(TimestampMixin, Base):
    __tablename__ = "access_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    request_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    requester_id: Mapped[str] = mapped_column(String(64), index=True)
    requester_upn: Mapped[str] = mapped_column(String(256))
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    role: Mapped[str] = mapped_column(String(64))
    justification: Mapped[str] = mapped_column(Text)
    duration_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    privileged: Mapped[bool] = mapped_column(default=False)
    required_stages: Mapped[int] = mapped_column(Integer, default=1)
    current_stage: Mapped[int] = mapped_column(Integer, default=1)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provisioning_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provisioning_attempts: Mapped[int] = mapped_column(Integer, default=0)
    approvals: Mapped[list["Approval"]] = relationship(back_populates="access_request", order_by="Approval.stage",
                                                       cascade="all, delete-orphan")
    application: Mapped[Application] = relationship()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    access_request_id: Mapped[str] = mapped_column(ForeignKey("access_requests.id"), index=True)
    stage: Mapped[int] = mapped_column(Integer, default=1)
    approver_id: Mapped[str] = mapped_column(String(64), index=True)
    approver_upn: Mapped[str] = mapped_column(String(256))
    approver_reason: Mapped[str] = mapped_column(String(64), default="manager")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    callback_token_hash: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_request: Mapped[AccessRequest] = relationship(back_populates="approvals")


class AccessGrant(Base):
    """Record of access granted by a provisioning adapter (mock grants are simulated only)."""
    __tablename__ = "access_grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    access_request_id: Mapped[str] = mapped_column(ForeignKey("access_requests.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))  # mock | entra
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    recipient_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="sent")
    related_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- governance
class AuditLog(Base):
    """Append-only, hash-chained audit trail."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))   # user | service | system | agent
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(16))      # success | failure | denied
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("principal_id", "scope", "key", name="uq_idem"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    principal_id: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- knowledge
class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(16))          # local | sharepoint
    source_uri: Mapped[str] = mapped_column(String(512), unique=True)
    title: Mapped[str] = mapped_column(String(256))
    allowed_groups: Mapped[list] = mapped_column(JSON, default=list)
    sensitivity: Mapped[str] = mapped_column(String(32), default="General")
    content_hash: Mapped[str] = mapped_column(String(64))
    injection_flags: Mapped[int] = mapped_column(Integer, default=0)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(256), default="")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    norm: Mapped[float] = mapped_column(Float, default=1.0)
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- agent
class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    principal_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    transcript: Mapped[list] = mapped_column(JSON, default=list)
