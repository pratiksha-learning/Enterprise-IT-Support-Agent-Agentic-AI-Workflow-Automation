"""Access Request workflow - the security-critical part of the system.

State machine::

    pending_approval --approve(stage<n)--> pending_approval (next stage)
    pending_approval --approve(final)----> approved -> provisioning -> provisioned | provisioning_failed
    pending_approval --reject------------> rejected
    pending_approval --cancel------------> cancelled
    pending_approval --timeout-----------> expired
    (approval initiation failure)        -> failed
    provisioned --expiry sweep-----------> expired (access revoked)

Hard rules enforced here (never delegated to the agent/LLM):
* the requester is the authenticated principal - never a field in the payload
* approvers are resolved server-side from the identity provider
* the requester can never approve their own request (any stage, any channel)
* only the *assigned* approver can decide, exactly once, before the due date
* Power Automate callbacks must present the one-time callback token (+ an allow-listed
  service principal token in Entra mode)
* provisioning grants exactly one group mapped to the requested role, time-bound
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import (AuthenticationError, AuthorizationError, ConflictError, DownstreamError, NotFoundError,
                        ValidationFailed)
from app.integrations.approvals import ApprovalProvider, ApprovalTask
from app.integrations.common import IntegrationError, with_retry
from app.integrations.identity import DirectoryUser, IdentityProvider
from app.integrations.provisioning import ProvisioningProvider
from app.models.orm import AccessGrant, AccessRequest, Application, Approval, as_utc, utcnow
from app.models.schemas import (AccessRequestCreate, ApprovalCallback, ApprovalDecisionValue, TicketCategory,
                                TicketCreate, TicketPriority)
from app.observability.telemetry import metrics_registry, traced
from app.repositories import AccessRequestRepository, ApplicationRepository, ApprovalRepository, GrantRepository
from app.security.principal import Principal
from app.security.rbac import Permission, has_permission
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from app.services.ticket_service import TicketService

logger = logging.getLogger(__name__)
SYSTEM = Principal(id="system:access-workflow", upn="system", display_name="Access workflow", auth_type="system")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_role(app: Application, role: str) -> dict | None:
    return next((r for r in app.roles if r["name"] == role), None)


class AccessRequestService:
    def __init__(self, db: Session, settings: Settings, identity: IdentityProvider, approvals: ApprovalProvider,
                 provisioning: ProvisioningProvider, notifications: NotificationService, audit: AuditService,
                 tickets: TicketService):
        self.db = db
        self.settings = settings
        self.identity = identity
        self.approvals = approvals
        self.provisioning = provisioning
        self.notifications = notifications
        self.audit = audit
        self.tickets = tickets
        self.apps = ApplicationRepository(db)
        self.requests = AccessRequestRepository(db)
        self.approval_repo = ApprovalRepository(db)
        self.grants = GrantRepository(db)

    # ================================================================== helpers
    def _retry(self, op: str, fn):
        return with_retry(op, fn, attempts=self.settings.downstream_max_attempts,
                          backoff=self.settings.downstream_backoff_seconds)

    def _fail_and_raise(self, exc: Exception) -> None:
        """Persist audit/failed state before surfacing an error (the request transaction would roll back)."""
        self.db.commit()
        raise exc

    def _lookup_user(self, user_id: str) -> DirectoryUser | None:
        try:
            return self._retry("identity_get_user", lambda: self.identity.get_user(user_id))
        except IntegrationError as exc:
            raise DownstreamError("Identity provider unavailable - please try again later",
                                  code="IDENTITY_PROVIDER_UNAVAILABLE", status_code=503) from exc

    def _resolve_approvers(self, principal: Principal, app: Application, privileged: bool,
                           notes: list[str]) -> list[tuple[DirectoryUser, str]]:
        try:
            manager = self._retry("identity_get_manager", lambda: self.identity.get_manager(principal.id))
        except IntegrationError as exc:
            self.audit.record(action="identity.manager_lookup_failed", target_type="user", target_id=principal.id,
                              actor=principal, outcome="failure", details={"error": str(exc)})
            self._fail_and_raise(DownstreamError("Could not resolve your manager - please try again later",
                                                 code="IDENTITY_PROVIDER_UNAVAILABLE", status_code=503))
        chain: list[tuple[DirectoryUser, str]] = []
        governance = self._lookup_user(self.settings.governance_approver_id)

        def eligible(user: DirectoryUser | None) -> bool:
            return bool(user and user.enabled and user.id != principal.id and all(user.id != c[0].id for c in chain))

        if eligible(manager):
            chain.append((manager, "manager"))
        else:
            reason = "self_approval_prevented" if manager and manager.id == principal.id else "no_manager"
            notes.append(reason)
            if not eligible(governance):
                self._fail_and_raise(ValidationFailed("No eligible approver could be found for this request",
                                                      code="NO_ELIGIBLE_APPROVER"))
            chain.append((governance, "governance_fallback"))
        if privileged:
            owner = self._lookup_user(app.owner_id)
            if eligible(owner):
                chain.append((owner, "application_owner"))
            elif eligible(governance):
                chain.append((governance, "governance_fallback"))
            else:
                self._fail_and_raise(ValidationFailed("No independent second-stage approver is available",
                                                      code="NO_ELIGIBLE_APPROVER"))
        return chain

    def _start_approval(self, req: AccessRequest, approver: DirectoryUser, reason: str, stage: int) -> Approval:
        token = secrets.token_urlsafe(32)
        approval = self.approval_repo.add(Approval(
            access_request_id=req.id, stage=stage, approver_id=approver.id, approver_upn=approver.upn,
            approver_reason=reason, status="pending", provider=self.approvals.name,
            callback_token_hash=_hash_token(token),
            due_at=utcnow() + timedelta(hours=self.settings.approval_timeout_hours)))
        app = self.apps.get(req.application_id)
        task = ApprovalTask(
            approval_id=approval.id, access_request_id=req.id, request_number=req.request_number, stage=stage,
            requester_upn=req.requester_upn, requester_display_name=req.requester_upn,
            approver_id=approver.id, approver_upn=approver.upn, application_name=app.name if app else req.application_id,
            role=req.role, privileged=req.privileged, duration_days=req.duration_days,
            justification=req.justification, due_at_iso=as_utc(approval.due_at).isoformat(),
            callback_url=f"{self.settings.public_base_url}/api/v1/approvals/callback", callback_token=token)
        try:
            with traced("approval.start", provider=self.approvals.name, stage=stage):
                approval.provider_reference = self._retry("approval_start", lambda: self.approvals.start(task))
        except IntegrationError as exc:
            approval.status = "cancelled"
            req.status = "failed"
            req.failure_reason = "Approval service unavailable"
            self.audit.record(action="approval.initiation_failed", target_type="access_request",
                              target_id=req.request_number, actor=SYSTEM, outcome="failure",
                              details={"provider": self.approvals.name, "error": str(exc), "stage": stage})
            self._fail_and_raise(DownstreamError("The approval service is unavailable. Your request was not "
                                                 "submitted for approval - please try again later.",
                                                 code="APPROVAL_SERVICE_UNAVAILABLE", status_code=503))
        if hasattr(self.approvals, "issued_tokens"):   # mock provider keeps tokens to simulate the flow callback
            self.approvals.issued_tokens[approval.id] = token
        self.audit.record(action="approval.requested", target_type="access_request", target_id=req.request_number,
                          actor=SYSTEM, details={"approval_id": approval.id, "stage": stage, "approver_id": approver.id,
                                                 "approver_reason": reason, "provider": self.approvals.name})
        self.notifications.notify(approver.id, approver.upn, f"Approval needed: {req.request_number}",
                                  f"{req.requester_upn} requests '{req.role}' on {task.application_name} for "
                                  f"{req.duration_days} days (stage {stage}/{req.required_stages}). "
                                  f"Justification: {req.justification}", related_id=req.request_number)
        return approval

    # ================================================================== catalog
    def catalog(self) -> list[Application]:
        return self.apps.list()

    # ================================================================== submit
    def submit(self, principal: Principal, data: AccessRequestCreate) -> AccessRequest:
        with traced("access_request.submit", application=data.application_id, role=data.role):
            app = self.apps.get(data.application_id)
            if not app:
                raise ValidationFailed("Unknown application", code="UNKNOWN_APPLICATION",
                                       details=[{"allowed": [a.id for a in self.apps.list()]}])
            role = find_role(app, data.role)
            if not role:
                raise ValidationFailed(f"Role '{data.role}' does not exist for {app.name}", code="UNKNOWN_ROLE",
                                       details=[{"allowed": [r["name"] for r in app.roles]}])
            max_days = min(app.max_duration_days, role.get("max_duration_days", app.max_duration_days))
            if data.duration_days > max_days:
                raise ValidationFailed(f"Maximum duration for {app.name}/{role['name']} is {max_days} days",
                                       code="DURATION_EXCEEDS_POLICY", details=[{"max_duration_days": max_days}])
            existing = self.requests.find_active(principal.id, app.id, role["name"])
            if existing:
                raise ConflictError("You already have an active request or grant for this application role",
                                    code="DUPLICATE_REQUEST", details=[{"request_number": existing.request_number,
                                                                        "status": existing.status}])
            requester = self._lookup_user(principal.id)
            if not requester or not requester.enabled:
                raise AuthorizationError("Your account is not eligible to request access", code="REQUESTER_NOT_ELIGIBLE")

            privileged = bool(role.get("privileged"))
            notes: list[str] = []
            chain = self._resolve_approvers(principal, app, privileged, notes)
            req = self.requests.add(AccessRequest(
                request_number=self.requests.next_number(), requester_id=principal.id, requester_upn=principal.upn,
                application_id=app.id, role=role["name"], justification=data.justification,
                duration_days=data.duration_days, status="pending_approval", privileged=privileged,
                required_stages=len(chain), current_stage=1))
            self.audit.record(action="access_request.submitted", target_type="access_request",
                              target_id=req.request_number, actor=principal,
                              details={"application": app.id, "role": role["name"], "duration_days": data.duration_days,
                                       "privileged": privileged, "approval_chain": [c[0].id for c in chain]})
            for reason in notes:   # e.g. directory says the requester is their own manager -> rerouted
                self.audit.record(action=f"approval.{reason}", target_type="access_request",
                                  target_id=req.request_number, actor=principal,
                                  outcome="denied" if reason == "self_approval_prevented" else "success",
                                  details={"rerouted_to": chain[0][0].id})
            self._start_approval(req, chain[0][0], chain[0][1], stage=1)
            metrics_registry.inc("access_requests", outcome="submitted", privileged=str(privileged).lower())
            self.db.flush()
            self.db.refresh(req)
            return req

    # ================================================================== read
    def _visible(self, principal: Principal, req: AccessRequest | None) -> AccessRequest:
        if req is None:
            raise NotFoundError("Access request not found", code="ACCESS_REQUEST_NOT_FOUND")
        if (req.requester_id == principal.id or has_permission(principal, Permission.ACCESS_READ_ALL)
                or any(a.approver_id == principal.id for a in req.approvals)):
            return req
        raise NotFoundError("Access request not found", code="ACCESS_REQUEST_NOT_FOUND")

    def get(self, principal: Principal, ref: str) -> AccessRequest:
        return self._visible(principal, self.requests.get_by_ref(ref))

    def list(self, principal: Principal, status: str | None, mine_only: bool, limit: int, offset: int):
        requester = principal.id if (mine_only or not has_permission(principal, Permission.ACCESS_READ_ALL)) else None
        return self.requests.list(requester_id=requester, status=status, limit=limit, offset=offset)

    def pending_approvals(self, principal: Principal) -> list[Approval]:
        return self.approval_repo.pending_for(principal.id)

    # ================================================================== cancel
    def cancel(self, principal: Principal, ref: str) -> AccessRequest:
        req = self.get(principal, ref)
        if req.requester_id != principal.id:
            raise AuthorizationError("Only the requester can cancel a request", code="CANCEL_FORBIDDEN")
        if req.status != "pending_approval":
            raise ConflictError(f"Request is '{req.status}' and can no longer be cancelled", code="REQUEST_NOT_PENDING")
        req.status = "cancelled"
        for a in req.approvals:
            if a.status == "pending":
                a.status = "cancelled"
        self.audit.record(action="access_request.cancelled", target_type="access_request",
                          target_id=req.request_number, actor=principal)
        return req

    # ================================================================== decisions
    def decide_direct(self, principal: Principal, approval_id: str, decision: ApprovalDecisionValue,
                      comment: str | None) -> AccessRequest:
        """Decision by an authenticated approver (local mock approval service / custom Teams card)."""
        if not self.approvals.supports_direct_decisions:
            raise AuthorizationError("Decisions are collected by Power Automate approvals in this environment",
                                     code="DIRECT_DECISION_DISABLED")
        approval = self.approval_repo.get(approval_id)
        if approval is None:
            raise NotFoundError("Approval not found", code="APPROVAL_NOT_FOUND")
        return self._apply_decision(approval, principal.id, principal, decision, comment, channel="mock_ui")

    def decide_callback(self, caller: Principal | None, token: str | None, payload: ApprovalCallback) -> AccessRequest:
        """Decision posted by the Power Automate approval flow (or a simulated flow in mock mode)."""
        approval = self.approval_repo.get(payload.approval_id)
        if approval is None:
            raise NotFoundError("Approval not found", code="APPROVAL_NOT_FOUND")
        if not token or not hmac.compare_digest(_hash_token(token), approval.callback_token_hash):
            self.audit.record(action="approval.callback_rejected", target_type="approval", target_id=approval.id,
                              actor_id=caller.id if caller else "anonymous", actor_type="service", outcome="denied",
                              details={"reason": "invalid_callback_token"})
            self._fail_and_raise(AuthenticationError("Invalid callback token", code="INVALID_CALLBACK_TOKEN"))
        # the responder identity reported by the Approvals connector must be the assigned approver
        # (checked in _apply_decision against both object id and UPN)
        actor = caller or Principal(id="power-automate", upn="flow", display_name="Power Automate", auth_type="service")
        req = self._apply_decision(approval, payload.responder_id, actor, payload.decision, payload.comment,
                                   channel="power_automate", responder_upn=payload.responder_upn)
        approval.callback_token_hash = "consumed"
        return req

    def _apply_decision(self, approval: Approval, decider_id: str, actor: Principal,
                        decision: ApprovalDecisionValue, comment: str | None, *, channel: str,
                        responder_upn: str | None = None) -> AccessRequest:
        req = approval.access_request
        base = {"approval_id": approval.id, "stage": approval.stage, "decider_id": decider_id, "channel": channel}
        if approval.status != "pending":
            raise ConflictError(f"Approval already {approval.status}", code="APPROVAL_ALREADY_DECIDED")
        if req.status != "pending_approval":
            raise ConflictError(f"Request is '{req.status}'", code="REQUEST_NOT_PENDING")
        if decider_id == req.requester_id:
            self.audit.record(action="approval.self_approval_blocked", target_type="access_request",
                              target_id=req.request_number, actor=actor, outcome="denied", details=base)
            metrics_registry.inc("approvals_blocked", reason="self_approval")
            self._fail_and_raise(AuthorizationError("You cannot approve your own access request",
                                                    code="SELF_APPROVAL_FORBIDDEN"))
        upn_ok = responder_upn is None or responder_upn.lower() == approval.approver_upn.lower()
        if decider_id != approval.approver_id or not upn_ok:
            self.audit.record(action="approval.unauthorized_decision_blocked", target_type="access_request",
                              target_id=req.request_number, actor=actor, outcome="denied", details=base)
            metrics_registry.inc("approvals_blocked", reason="not_assigned_approver")
            self._fail_and_raise(AuthorizationError("Only the assigned approver can decide this request",
                                                    code="NOT_ASSIGNED_APPROVER"))
        now = utcnow()
        if as_utc(approval.due_at) < now:
            approval.status = "expired"
            req.status = "expired"
            self.audit.record(action="approval.expired", target_type="access_request", target_id=req.request_number,
                              actor=SYSTEM, details=base)
            self._fail_and_raise(ConflictError("This approval has expired", code="APPROVAL_EXPIRED"))

        approval.status = "approved" if decision == ApprovalDecisionValue.approve else "rejected"
        approval.decided_at = now
        approval.decided_by_id = decider_id
        approval.comment = comment
        approval.decision_channel = channel
        duration_s = (now - as_utc(approval.created_at)).total_seconds()
        metrics_registry.inc("approvals_completed", outcome=approval.status, channel=channel)
        metrics_registry.observe("approval_duration_s", duration_s, outcome=approval.status)
        self.audit.record(action=f"approval.{approval.status}", target_type="access_request",
                          target_id=req.request_number, actor=actor,
                          details={**base, "comment": comment or ""})

        if approval.status == "rejected":
            req.status = "rejected"
            self.notifications.notify(req.requester_id, req.requester_upn, f"Access request {req.request_number} rejected",
                                      f"Your request for '{req.role}' on {req.application_id} was rejected."
                                      + (f" Comment: {comment}" if comment else ""), related_id=req.request_number)
            return req

        if approval.stage < req.required_stages:
            req.current_stage = approval.stage + 1
            next_approver, reason = self._next_stage_approver(req, approval.stage + 1)
            self._start_approval(req, next_approver, reason, stage=approval.stage + 1)
            return req

        req.status = "approved"
        self._provision(req)
        return req

    def _next_stage_approver(self, req: AccessRequest, stage: int) -> tuple[DirectoryUser, str]:
        """Re-derive the stage-N approver server-side (application owner, or governance fallback)."""
        app = self.apps.get(req.application_id)
        previous = {a.approver_id for a in req.approvals}
        for candidate_id, reason in ((app.owner_id, "application_owner"),
                                     (self.settings.governance_approver_id, "governance_fallback")):
            if candidate_id == req.requester_id or candidate_id in previous:
                continue
            user = self._lookup_user(candidate_id)
            if user and user.enabled:
                return user, reason
        self._fail_and_raise(ValidationFailed("No independent approver for stage %d" % stage,
                                              code="NO_ELIGIBLE_APPROVER"))
        raise AssertionError  # pragma: no cover

    # ================================================================== provisioning
    def _provision(self, req: AccessRequest) -> None:
        app = self.apps.get(req.application_id)
        role = find_role(app, req.role)
        req.status = "provisioning"
        req.provisioning_attempts += 1
        expires = utcnow() + timedelta(days=req.duration_days)
        try:
            with traced("access.provision", provider=self.provisioning.name, application=app.id):
                result = self._retry("provisioning_grant",
                                     lambda: self.provisioning.grant(req.requester_id, role["group_id"]))
        except IntegrationError as exc:
            req.status = "provisioning_failed"
            req.failure_reason = "Provisioning service unavailable"
            metrics_registry.inc("provisioning", outcome="failure")
            self.audit.record(action="access.provisioning_failed", target_type="access_request",
                              target_id=req.request_number, actor=SYSTEM, outcome="failure",
                              details={"provider": self.provisioning.name, "error": str(exc),
                                       "attempt": req.provisioning_attempts})
            ticket = self.tickets.create(SYSTEM, TicketCreate(
                title=f"Access provisioning failed for {req.request_number}",
                description=(f"Automated provisioning of '{req.role}' on {app.name} failed after "
                             f"{self.settings.downstream_max_attempts} attempts. The request was approved. "
                             "Please investigate and retry provisioning."),
                category=TicketCategory.access, priority=TicketPriority.P3),
                source="access-automation", related_access_request_id=req.id,
                requester=(req.requester_id, req.requester_upn))
            self.notifications.notify(req.requester_id, req.requester_upn,
                                      f"Access request {req.request_number}: approved, provisioning delayed",
                                      f"Your request was approved but granting access failed. IT has been notified "
                                      f"({ticket.ticket_number}).", related_id=req.request_number)
            return
        req.status = "provisioned"
        req.failure_reason = None
        req.provisioning_reference = result.reference
        req.access_expires_at = expires
        self.grants.add(AccessGrant(access_request_id=req.id, user_id=req.requester_id, application_id=app.id,
                                    role=req.role, group_id=role["group_id"], mode=result.mode, expires_at=expires))
        metrics_registry.inc("provisioning", outcome="success")
        self.audit.record(action="access.provisioned", target_type="access_request", target_id=req.request_number,
                          actor=SYSTEM, details={"provider": self.provisioning.name, "group_id": role["group_id"],
                                                 "simulated": result.simulated, "expires_at": expires.isoformat()})
        note = " (SIMULATED - local mock mode, no real access was granted)" if result.simulated else ""
        self.notifications.notify(req.requester_id, req.requester_upn, f"Access granted: {req.request_number}",
                                  f"You now have '{req.role}' on {app.name} until {expires.date().isoformat()}{note}.",
                                  related_id=req.request_number)

    def retry_provisioning(self, principal: Principal, ref: str) -> AccessRequest:
        if not has_permission(principal, Permission.ACCESS_ADMIN):
            raise AuthorizationError("Only administrators can retry provisioning", code="FORBIDDEN")
        req = self.get(principal, ref)
        if req.status != "provisioning_failed":
            raise ConflictError("Only failed provisioning can be retried", code="NOT_RETRYABLE")
        self.audit.record(action="access.provisioning_retry", target_type="access_request",
                          target_id=req.request_number, actor=principal)
        self._provision(req)
        return req

    # ================================================================== sweeps
    def expire_overdue(self) -> dict:
        now = utcnow()
        expired_approvals = 0
        for approval in self.approval_repo.overdue(now):
            approval.status = "expired"
            req = approval.access_request
            if req.status == "pending_approval":
                req.status = "expired"
                self.notifications.notify(req.requester_id, req.requester_upn,
                                          f"Access request {req.request_number} expired",
                                          "No decision was made before the approval deadline.",
                                          related_id=req.request_number)
            self.audit.record(action="approval.expired", target_type="access_request", target_id=req.request_number,
                              actor=SYSTEM)
            expired_approvals += 1
        revoked = 0
        for grant in self.grants.active_expired(now):
            try:
                self._retry("provisioning_revoke", lambda g=grant: self.provisioning.revoke(g.user_id, g.group_id))
            except IntegrationError:
                continue
            grant.revoked_at = now
            req = self.requests.get(grant.access_request_id)
            if req:
                req.status = "expired"
            self.audit.record(action="access.revoked_on_expiry", target_type="access_request",
                              target_id=req.request_number if req else grant.access_request_id, actor=SYSTEM,
                              details={"group_id": grant.group_id})
            revoked += 1
        return {"approvals_expired": expired_approvals, "grants_revoked": revoked}
