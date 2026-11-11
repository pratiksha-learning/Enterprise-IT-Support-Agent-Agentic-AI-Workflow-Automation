"""REST API (v1). Thin controllers: validate -> authorise -> call service -> serialise."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agents.orchestrator import AgentOrchestrator
from app.api.deps import current_principal, get_container, get_services, optional_principal, require
from app.config import AuthMode
from app.container import Container, Services
from app.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.models.schemas import (AccessRequestCreate, AccessRequestList, AccessRequestOut, ApplicationOut,
                                ApprovalCallback, ApprovalDecision, AuditEntryOut, AuditList, AuditVerifyResult,
                                ChatRequest, ChatResponse, DevTokenRequest, DirectoryUserOut, FaultInjectionRequest,
                                IngestResult, KnowledgeAnswer, KnowledgeDocumentOut, KnowledgeQuery, NotificationOut,
                                PendingApprovalOut, ApprovalOut, PrincipalOut, TicketCreate, TicketEventOut,
                                TicketList, TicketOut, TicketStatusOut, TicketStatusUpdate, TokenResponse)
from app.observability.telemetry import metrics_registry
from app.repositories import NotificationRepository
from app.security.principal import Principal
from app.security.rbac import Permission, has_permission, permissions_for
from app.services.idempotency_service import payload_hash, validate_key

IdemHeader = Annotated[str | None, Header(alias="Idempotency-Key", description="Unique key per logical request")]


def _principal_out(p: Principal) -> PrincipalOut:
    return PrincipalOut(id=p.id, upn=p.upn, display_name=p.display_name, roles=sorted(p.roles),
                        groups=sorted(p.groups), auth_type=p.auth_type)


# =========================================================================== health
health = APIRouter(tags=["health"])


@health.get("/health/live")
def live():
    return {"status": "ok"}


@health.get("/health/ready")
def ready(request: Request):
    with request.app.state.session_factory() as db:
        db.execute(text("SELECT 1"))
    c: Container = request.app.state.container
    return {"status": "ready", "auth_mode": c.settings.auth_mode.value,
            "adapters": {"identity": c.identity.name, "approvals": c.approvals.name,
                         "provisioning": c.provisioning.name, "notifications": c.notifier.name,
                         "embedder": c.embedder.name, "answer_generator": c.generator.name}}


# =========================================================================== auth
auth = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@auth.post("/dev-token", response_model=TokenResponse,
           summary="[MOCK MODE ONLY] Issue a signed demo token for a fictional persona")
def dev_token(body: DevTokenRequest, c: Container = Depends(get_container)):
    if c.settings.auth_mode != AuthMode.MOCK or c.mock_tokens is None:
        raise NotFoundError("Not available", code="NOT_FOUND")
    token, ttl, principal = c.mock_tokens.issue(body.user_id)
    return TokenResponse(access_token=token, expires_in=ttl, user=_principal_out(principal))


@auth.get("/demo-users", response_model=list[DirectoryUserOut], summary="[MOCK MODE ONLY] List demo personas")
def demo_users(c: Container = Depends(get_container)):
    if c.settings.auth_mode != AuthMode.MOCK:
        raise NotFoundError("Not available", code="NOT_FOUND")
    return [DirectoryUserOut(id=u["id"], display_name=u["display_name"], upn=u["upn"], roles=u["roles"],
                             title=u.get("title", "")) for u in c.directory.list()]


@auth.get("/me")
def me(p: Principal = Depends(current_principal)):
    return {**_principal_out(p).model_dump(), "permissions": sorted(x.value for x in permissions_for(p))}


# =========================================================================== knowledge
knowledge = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@knowledge.post("/query", response_model=KnowledgeAnswer)
def query_knowledge(body: KnowledgeQuery, p: Principal = Depends(require(Permission.KNOWLEDGE_QUERY)),
                    s: Services = Depends(get_services)):
    return s.knowledge.answer(p, body.question, body.top_k)


@knowledge.get("/documents", response_model=list[KnowledgeDocumentOut],
               summary="Documents visible to the caller (security-trimmed)")
def list_documents(p: Principal = Depends(require(Permission.KNOWLEDGE_QUERY)), s: Services = Depends(get_services)):
    return s.knowledge.visible_documents(p)


@knowledge.post("/ingest", response_model=IngestResult)
def ingest(p: Principal = Depends(require(Permission.KNOWLEDGE_INGEST)), s: Services = Depends(get_services),
           c: Container = Depends(get_container)):
    result = s.knowledge.ingest(c.knowledge_source())
    s.audit.record(action="knowledge.ingested", target_type="knowledge", target_id=c.settings.knowledge_source,
                   actor=p, details=result.model_dump())
    s.commit()
    return result


# =========================================================================== tickets
tickets = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


@tickets.post("", response_model=TicketOut, status_code=201)
def create_ticket(body: TicketCreate, idempotency_key: IdemHeader = None,
                  p: Principal = Depends(require(Permission.TICKET_CREATE)), s: Services = Depends(get_services)):
    key = validate_key(idempotency_key, required=False)
    h = payload_hash(body.model_dump(mode="json"))
    if key and (prior := s.idempotency.lookup(p.id, "tickets.create", key, h)):
        return JSONResponse(prior.response_body, status_code=prior.response_status,
                            headers={"Idempotent-Replayed": "true"})
    out = TicketOut.model_validate(s.tickets.create(p, body)).model_dump(mode="json")
    if key:
        s.idempotency.store(p.id, "tickets.create", key, h, 201, out)
    s.commit()
    return JSONResponse(out, status_code=201)


@tickets.get("", response_model=TicketList)
def list_tickets(status: str | None = Query(default=None, pattern=r"^[a-z_]+$"), mine: bool = False,
                 limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                 p: Principal = Depends(require(Permission.TICKET_READ_OWN)), s: Services = Depends(get_services)):
    items, total = s.tickets.list(p, status, mine, limit, offset)
    return TicketList(items=[TicketOut.model_validate(t) for t in items], total=total)


TicketNumber = Annotated[str, Path(pattern=r"^[Ii][Nn][Cc]\d{7}$", examples=["INC1001001"])]


@tickets.get("/{ticket_number}", response_model=TicketOut)
def get_ticket(ticket_number: TicketNumber,
               p: Principal = Depends(require(Permission.TICKET_READ_OWN)), s: Services = Depends(get_services)):
    return s.tickets.get(p, ticket_number)


@tickets.get("/{ticket_number}/status", response_model=TicketStatusOut)
def get_ticket_status(ticket_number: TicketNumber, p: Principal = Depends(require(Permission.TICKET_READ_OWN)),
                      s: Services = Depends(get_services)):
    return s.tickets.get(p, ticket_number)


@tickets.patch("/{ticket_number}/status", response_model=TicketOut)
def update_ticket_status(ticket_number: TicketNumber, body: TicketStatusUpdate, p: Principal = Depends(current_principal),
                         s: Services = Depends(get_services)):
    ticket = s.tickets.update_status(p, ticket_number, body.status, body.comment)
    out = TicketOut.model_validate(ticket)
    s.commit()
    return out


@tickets.get("/{ticket_number}/history", response_model=list[TicketEventOut])
def ticket_history(ticket_number: TicketNumber, p: Principal = Depends(require(Permission.TICKET_READ_OWN)),
                   s: Services = Depends(get_services)):
    return s.tickets.get(p, ticket_number).events


# =========================================================================== access requests
access = APIRouter(prefix="/api/v1", tags=["access requests"])


@access.get("/access/applications", response_model=list[ApplicationOut])
def list_applications(p: Principal = Depends(require(Permission.ACCESS_REQUEST)), s: Services = Depends(get_services)):
    return [ApplicationOut(id=a.id, name=a.name, description=a.description, max_duration_days=a.max_duration_days,
                           roles=[{k: r.get(k, a.max_duration_days if k == "max_duration_days" else None)
                                   for k in ("name", "description", "level", "privileged", "max_duration_days")}
                                  for r in a.roles]) for a in s.access.catalog()]


@access.post("/access-requests", response_model=AccessRequestOut, status_code=201,
             responses={409: {"description": "Duplicate active request"}, 503: {"description": "Dependency down"}})
def submit_access_request(body: AccessRequestCreate, idempotency_key: IdemHeader = None,
                          p: Principal = Depends(require(Permission.ACCESS_REQUEST)),
                          s: Services = Depends(get_services)):
    key = validate_key(idempotency_key, required=True)
    h = payload_hash(body.model_dump(mode="json"))
    if prior := s.idempotency.lookup(p.id, "access_requests.create", key, h):
        return JSONResponse(prior.response_body, status_code=prior.response_status,
                            headers={"Idempotent-Replayed": "true"})
    out = AccessRequestOut.model_validate(s.access.submit(p, body)).model_dump(mode="json")
    s.idempotency.store(p.id, "access_requests.create", key, h, 201, out)
    s.commit()
    return JSONResponse(out, status_code=201)


@access.get("/access-requests", response_model=AccessRequestList)
def list_access_requests(status: str | None = Query(default=None, pattern=r"^[a-z_]+$"), mine: bool = False,
                         limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                         p: Principal = Depends(require(Permission.ACCESS_READ_OWN)),
                         s: Services = Depends(get_services)):
    items, total = s.access.list(p, status, mine, limit, offset)
    return AccessRequestList(items=[AccessRequestOut.model_validate(r) for r in items], total=total)


@access.get("/access-requests/{ref}", response_model=AccessRequestOut)
def get_access_request(ref: str, p: Principal = Depends(current_principal), s: Services = Depends(get_services)):
    return s.access.get(p, ref)


@access.post("/access-requests/{ref}/cancel", response_model=AccessRequestOut)
def cancel_access_request(ref: str, p: Principal = Depends(require(Permission.ACCESS_REQUEST)),
                          s: Services = Depends(get_services)):
    out = AccessRequestOut.model_validate(s.access.cancel(p, ref))
    s.commit()
    return out


@access.post("/access-requests/{ref}/retry-provisioning", response_model=AccessRequestOut)
def retry_provisioning(ref: str, p: Principal = Depends(require(Permission.ACCESS_ADMIN)),
                       s: Services = Depends(get_services)):
    out = AccessRequestOut.model_validate(s.access.retry_provisioning(p, ref))
    s.commit()
    return out


@access.get("/access-requests/{ref}/audit", response_model=list[AuditEntryOut])
def access_request_audit(ref: str, p: Principal = Depends(current_principal), s: Services = Depends(get_services)):
    req = s.access.get(p, ref)   # visibility check (requester / approver / IT)
    items, _ = s.audit.repo.query(target_ids=[req.request_number, req.id], limit=200)
    return items


# =========================================================================== approvals
approvals = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@approvals.get("/pending", response_model=list[PendingApprovalOut])
def pending_approvals(p: Principal = Depends(require(Permission.APPROVAL_DECIDE)), s: Services = Depends(get_services)):
    return [PendingApprovalOut(approval=ApprovalOut.model_validate(a),
                               request=AccessRequestOut.model_validate(a.access_request))
            for a in s.access.pending_approvals(p)]


@approvals.post("/{approval_id}/decision", response_model=AccessRequestOut,
                summary="Approver decision (local mock approval service)")
def decide(approval_id: str, body: ApprovalDecision, p: Principal = Depends(require(Permission.APPROVAL_DECIDE)),
           s: Services = Depends(get_services)):
    out = AccessRequestOut.model_validate(s.access.decide_direct(p, approval_id, body.decision, body.comment))
    s.commit()
    return out


@approvals.post("/callback", response_model=AccessRequestOut,
                summary="Decision callback from the Power Automate approval flow")
def approval_callback(body: ApprovalCallback, request: Request,
                      x_callback_token: Annotated[str | None, Header(alias="X-Callback-Token")] = None,
                      caller: Principal | None = Depends(optional_principal), s: Services = Depends(get_services),
                      c: Container = Depends(get_container)):
    if c.settings.auth_mode == AuthMode.ENTRA:
        # production: the flow must authenticate as an allow-listed service principal with the callback app role
        if caller is None:
            raise AuthenticationError("Service authentication required", code="UNAUTHENTICATED")
        if caller.auth_type != "service" or not has_permission(caller, Permission.APPROVAL_CALLBACK):
            raise AuthorizationError("Caller may not post approval decisions", code="FORBIDDEN")
    out = AccessRequestOut.model_validate(s.access.decide_callback(caller, x_callback_token, body))
    s.commit()
    return out


@approvals.post("/{approval_id}/simulate-flow-callback", response_model=AccessRequestOut,
                summary="[MOCK MODE ONLY] Simulate Power Automate posting the approver's response")
def simulate_callback(approval_id: str, body: ApprovalDecision,
                      p: Principal = Depends(require(Permission.APPROVAL_DECIDE)), s: Services = Depends(get_services),
                      c: Container = Depends(get_container)):
    tokens = getattr(c.approvals, "issued_tokens", None)
    if c.settings.auth_mode != AuthMode.MOCK or tokens is None:
        raise NotFoundError("Not available", code="NOT_FOUND")
    payload = ApprovalCallback(approval_id=approval_id, decision=body.decision, responder_id=p.id,
                               responder_upn=p.upn, comment=body.comment)
    out = AccessRequestOut.model_validate(s.access.decide_callback(None, tokens.get(approval_id), payload))
    s.commit()
    return out


# =========================================================================== audit & notifications
audit = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@audit.get("", response_model=AuditList)
def list_audit(target_id: str | None = None, actor_id: str | None = None, action: str | None = None,
               limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
               p: Principal = Depends(require(Permission.AUDIT_READ)), s: Services = Depends(get_services)):
    items, total = s.audit.repo.query(target_id=target_id, actor_id=actor_id, action=action, limit=limit, offset=offset)
    return AuditList(items=[AuditEntryOut.model_validate(i) for i in items], total=total)


@audit.get("/verify", response_model=AuditVerifyResult)
def verify_audit(p: Principal = Depends(require(Permission.AUDIT_READ)), s: Services = Depends(get_services)):
    valid, n, bad = s.audit.verify()
    return AuditVerifyResult(valid=valid, entries_checked=n, first_invalid_id=bad)


notifications = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@notifications.get("/me", response_model=list[NotificationOut])
def my_notifications(p: Principal = Depends(current_principal), s: Services = Depends(get_services)):
    return NotificationRepository(s.db).for_recipient(p.id)


# =========================================================================== agent
agent = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@agent.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, p: Principal = Depends(require(Permission.AGENT_CHAT)), s: Services = Depends(get_services)):
    resp = AgentOrchestrator(s.db, s).chat(p, body.message, body.conversation_id)
    s.commit()
    return resp


# =========================================================================== admin
admin = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin.get("/metrics")
def get_metrics(p: Principal = Depends(require(Permission.ADMIN_METRICS))):
    return metrics_registry.snapshot()


@admin.get("/fault-injection")
def get_faults(p: Principal = Depends(require(Permission.ADMIN_FAULTS)), c: Container = Depends(get_container)):
    return c.faults.state()


@admin.post("/fault-injection", summary="[MOCK MODE ONLY] Simulate a downstream outage")
def set_fault(body: FaultInjectionRequest, p: Principal = Depends(require(Permission.ADMIN_FAULTS)),
              c: Container = Depends(get_container), s: Services = Depends(get_services)):
    if c.settings.auth_mode != AuthMode.MOCK:
        raise NotFoundError("Not available", code="NOT_FOUND")
    c.faults.set(body.component, body.enabled)
    s.audit.record(action="admin.fault_injection", target_type="component", target_id=body.component, actor=p,
                   details={"enabled": body.enabled})
    s.commit()
    return c.faults.state()


@admin.post("/maintenance/expire", summary="Expire overdue approvals and revoke expired grants")
def run_expiry(p: Principal = Depends(require(Permission.ACCESS_ADMIN)), s: Services = Depends(get_services)):
    result = s.access.expire_overdue()
    s.commit()
    return result


ALL_ROUTERS = [health, auth, knowledge, tickets, access, approvals, audit, notifications, agent, admin]
