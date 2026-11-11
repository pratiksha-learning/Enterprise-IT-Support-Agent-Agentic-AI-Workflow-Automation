"""IT ticket management (the local mock ITSM system of record)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import AuthorizationError, ConflictError, NotFoundError
from app.models.orm import Ticket, TicketEvent
from app.models.schemas import TicketCreate, TicketStatus
from app.repositories import TicketRepository
from app.security.principal import Principal
from app.security.rbac import Permission, has_permission
from app.services.audit_service import AuditService

# allowed status transitions (state machine)
TRANSITIONS: dict[str, set[str]] = {
    "new": {"in_progress", "on_hold", "resolved", "cancelled"},
    "in_progress": {"on_hold", "resolved", "cancelled"},
    "on_hold": {"in_progress", "cancelled"},
    "resolved": {"closed", "in_progress"},
    "closed": set(),
    "cancelled": set(),
}


class TicketService:
    def __init__(self, db: Session, audit: AuditService):
        self.db = db
        self.repo = TicketRepository(db)
        self.audit = audit

    def create(self, principal: Principal, data: TicketCreate, source: str = "api",
               related_access_request_id: str | None = None, requester: tuple[str, str] | None = None) -> Ticket:
        # requester is always the authenticated principal; ``requester`` override is used only by
        # the system itself (e.g. auto-ticket on provisioning failure on behalf of the requester).
        req_id, req_upn = requester or (principal.id, principal.upn)
        ticket = self.repo.add(Ticket(
            ticket_number=self.repo.next_number(), title=data.title, description=data.description,
            category=data.category.value, priority=data.priority.value, status="new",
            requester_id=req_id, requester_upn=req_upn, source=source,
            related_access_request_id=related_access_request_id))
        self.repo.add_event(TicketEvent(ticket_id=ticket.id, from_status=None, to_status="new", actor_id=principal.id,
                                        comment="Ticket created"))
        self.audit.record(action="ticket.created", target_type="ticket", target_id=ticket.ticket_number,
                          actor=principal, details={"priority": ticket.priority, "category": ticket.category,
                                                    "source": source})
        return ticket

    def _visible(self, principal: Principal, ticket: Ticket | None) -> Ticket:
        # 404 (not 403) for tickets the caller may not see -> no ticket-number enumeration
        if ticket is None or (ticket.requester_id != principal.id
                              and not has_permission(principal, Permission.TICKET_READ_ALL)):
            raise NotFoundError("Ticket not found", code="TICKET_NOT_FOUND")
        return ticket

    def get(self, principal: Principal, number: str) -> Ticket:
        return self._visible(principal, self.repo.get_by_number(number))

    def list(self, principal: Principal, status: str | None, mine_only: bool, limit: int, offset: int):
        requester = principal.id if (mine_only or not has_permission(principal, Permission.TICKET_READ_ALL)) else None
        return self.repo.list(requester_id=requester, status=status, limit=limit, offset=offset)

    def update_status(self, principal: Principal, number: str, new_status: TicketStatus,
                      comment: str | None) -> Ticket:
        ticket = self.get(principal, number)
        is_agent = has_permission(principal, Permission.TICKET_UPDATE_STATUS)
        is_owner_cancel = (ticket.requester_id == principal.id and new_status == TicketStatus.cancelled
                           and has_permission(principal, Permission.TICKET_CANCEL_OWN))
        if not (is_agent or is_owner_cancel):
            self.audit.record(action="ticket.status_update_denied", target_type="ticket", target_id=number,
                              actor=principal, outcome="denied", details={"requested_status": new_status.value})
            raise AuthorizationError("Only IT agents can change ticket status (requesters may cancel their own)",
                                     code="TICKET_UPDATE_FORBIDDEN")
        if new_status.value not in TRANSITIONS[ticket.status]:
            raise ConflictError(f"Cannot move ticket from '{ticket.status}' to '{new_status.value}'",
                                code="INVALID_STATUS_TRANSITION",
                                details=[{"allowed": sorted(TRANSITIONS[ticket.status])}])
        old = ticket.status
        ticket.status = new_status.value
        if is_agent and not ticket.assigned_to:
            ticket.assigned_to = principal.id
        self.repo.add_event(TicketEvent(ticket_id=ticket.id, from_status=old, to_status=new_status.value,
                                        actor_id=principal.id, comment=comment))
        self.audit.record(action="ticket.status_updated", target_type="ticket", target_id=number, actor=principal,
                          details={"from": old, "to": new_status.value})
        self.db.flush()
        return ticket
