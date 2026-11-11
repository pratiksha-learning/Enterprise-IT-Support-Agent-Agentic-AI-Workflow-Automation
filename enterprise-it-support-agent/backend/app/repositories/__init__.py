"""Repository layer: all SQL lives here; services contain business rules only."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.orm import (AccessGrant, AccessRequest, Application, Approval, AuditLog, Conversation,
                            IdempotencyRecord, KnowledgeChunk, KnowledgeDocument, Notification, Ticket,
                            TicketEvent)


class TicketRepository:
    def __init__(self, db: Session):
        self.db = db

    def next_number(self) -> str:
        current = self.db.scalar(select(func.max(Ticket.id))) or 0
        return f"INC{1001000 + current + 1:07d}"

    def add(self, ticket: Ticket) -> Ticket:
        self.db.add(ticket)
        self.db.flush()
        return ticket

    def add_event(self, event: TicketEvent) -> None:
        self.db.add(event)

    def get_by_number(self, number: str) -> Ticket | None:
        return self.db.scalar(select(Ticket).where(Ticket.ticket_number == number.upper()))

    def list(self, requester_id: str | None = None, status: str | None = None, limit: int = 50,
             offset: int = 0) -> tuple[list[Ticket], int]:
        q = select(Ticket)
        if requester_id:
            q = q.where(Ticket.requester_id == requester_id)
        if status:
            q = q.where(Ticket.status == status)
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        items = self.db.scalars(q.order_by(Ticket.id.desc()).limit(limit).offset(offset)).all()
        return list(items), total


class ApplicationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, app_id: str) -> Application | None:
        return self.db.get(Application, app_id)

    def list(self) -> list[Application]:
        return list(self.db.scalars(select(Application).order_by(Application.name)).all())

    def upsert(self, app: Application) -> None:
        self.db.merge(app)


class AccessRequestRepository:
    ACTIVE = ("pending_approval", "approved", "provisioning", "provisioned")

    def __init__(self, db: Session):
        self.db = db

    def next_number(self) -> str:
        count = self.db.scalar(select(func.count(AccessRequest.id))) or 0
        return f"ACC{100000 + count + 1:06d}"

    def add(self, req: AccessRequest) -> AccessRequest:
        self.db.add(req)
        self.db.flush()
        return req

    def get(self, request_id: str) -> AccessRequest | None:
        return self.db.scalar(select(AccessRequest).options(selectinload(AccessRequest.approvals))
                              .where(AccessRequest.id == request_id))

    def get_by_ref(self, ref: str) -> AccessRequest | None:
        q = select(AccessRequest).options(selectinload(AccessRequest.approvals))
        if ref.upper().startswith("ACC"):
            return self.db.scalar(q.where(AccessRequest.request_number == ref.upper()))
        return self.db.scalar(q.where(AccessRequest.id == ref))

    def find_active(self, requester_id: str, application_id: str, role: str) -> AccessRequest | None:
        return self.db.scalar(select(AccessRequest).where(
            AccessRequest.requester_id == requester_id, AccessRequest.application_id == application_id,
            AccessRequest.role == role, AccessRequest.status.in_(self.ACTIVE)))

    def list(self, requester_id: str | None = None, status: str | None = None, limit: int = 50,
             offset: int = 0) -> tuple[list[AccessRequest], int]:
        q = select(AccessRequest)
        if requester_id:
            q = q.where(AccessRequest.requester_id == requester_id)
        if status:
            q = q.where(AccessRequest.status == status)
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        items = self.db.scalars(q.options(selectinload(AccessRequest.approvals))
                                .order_by(AccessRequest.created_at.desc()).limit(limit).offset(offset)).all()
        return list(items), total


class ApprovalRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, approval: Approval) -> Approval:
        self.db.add(approval)
        self.db.flush()
        return approval

    def get(self, approval_id: str) -> Approval | None:
        return self.db.get(Approval, approval_id)

    def pending_for(self, approver_id: str) -> list[Approval]:
        return list(self.db.scalars(select(Approval).where(Approval.approver_id == approver_id,
                                                           Approval.status == "pending")
                                    .order_by(Approval.created_at)).all())

    def overdue(self, now: datetime) -> list[Approval]:
        return list(self.db.scalars(select(Approval).where(Approval.status == "pending", Approval.due_at < now)).all())


class GrantRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, grant: AccessGrant) -> None:
        self.db.add(grant)

    def active_expired(self, now: datetime) -> list[AccessGrant]:
        return list(self.db.scalars(select(AccessGrant).where(AccessGrant.revoked_at.is_(None),
                                                              AccessGrant.expires_at < now)).all())

    def for_request(self, request_id: str) -> list[AccessGrant]:
        return list(self.db.scalars(select(AccessGrant).where(AccessGrant.access_request_id == request_id)).all())


class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, n: Notification) -> Notification:
        self.db.add(n)
        return n

    def for_recipient(self, recipient_id: str, limit: int = 50) -> list[Notification]:
        return list(self.db.scalars(select(Notification).where(Notification.recipient_id == recipient_id)
                                    .order_by(Notification.created_at.desc()).limit(limit)).all())


class AuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def last(self) -> AuditLog | None:
        return self.db.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))

    def add(self, entry: AuditLog) -> None:
        self.db.add(entry)
        self.db.flush()

    def query(self, *, target_id: str | None = None, target_ids: list[str] | None = None, actor_id: str | None = None,
              action: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[AuditLog], int]:
        q = select(AuditLog)
        if target_id:
            q = q.where(AuditLog.target_id == target_id)
        if target_ids:
            q = q.where(AuditLog.target_id.in_(target_ids))
        if actor_id:
            q = q.where(AuditLog.actor_id == actor_id)
        if action:
            q = q.where(AuditLog.action == action)
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        return list(self.db.scalars(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()), total

    def all_ordered(self) -> list[AuditLog]:
        return list(self.db.scalars(select(AuditLog).order_by(AuditLog.id)).all())


class IdempotencyRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, principal_id: str, scope: str, key: str) -> IdempotencyRecord | None:
        return self.db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.principal_id == principal_id, IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key))

    def add(self, record: IdempotencyRecord) -> None:
        self.db.add(record)
        self.db.flush()

    def delete(self, record: IdempotencyRecord) -> None:
        self.db.delete(record)
        self.db.flush()


class KnowledgeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_uri(self, uri: str) -> KnowledgeDocument | None:
        return self.db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.source_uri == uri))

    def add(self, doc: KnowledgeDocument) -> None:
        self.db.add(doc)
        self.db.flush()

    def delete(self, doc: KnowledgeDocument) -> None:
        self.db.delete(doc)
        self.db.flush()

    def documents(self) -> list[KnowledgeDocument]:
        return list(self.db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.title)).all())

    def chunks_for(self, document_ids: list[str]) -> list[KnowledgeChunk]:
        if not document_ids:
            return []
        return list(self.db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(document_ids))).all())

    def count(self) -> int:
        return self.db.scalar(select(func.count(KnowledgeDocument.id))) or 0


class ConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, conversation_id: str) -> Conversation | None:
        return self.db.get(Conversation, conversation_id)

    def add(self, conv: Conversation) -> Conversation:
        self.db.add(conv)
        self.db.flush()
        return conv
