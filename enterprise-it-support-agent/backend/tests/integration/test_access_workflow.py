"""End-to-end access request workflow: approval, rejection, security controls and failure handling."""
from datetime import timedelta

import httpx

from app.integrations.approvals import PowerAutomateApprovalProvider
from app.models.orm import AccessGrant, AccessRequest, Approval, Notification, Ticket, utcnow


def _actions(api, ref, user="u-alice"):
    return [e["action"] for e in api.get(f"/api/v1/access-requests/{ref}/audit", user).json()]


def _db(app):
    return app.state.session_factory()


# ------------------------------------------------------------------ happy paths
def test_successful_approval_provisions_access_and_notifies(api, app):
    r = api.submit_access("u-alice")
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending_approval" and req["requester_id"] == "u-alice"
    assert req["approvals"][0]["approver_id"] == "u-bob" and req["approvals"][0]["approver_reason"] == "manager"

    pending = api.pending("u-bob")
    assert len(pending) == 1 and pending[0]["request"]["request_number"] == req["request_number"]
    r = api.decide("u-bob", pending[0]["approval"]["id"], "approve", "Approved for Q4")
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "provisioned" and done["provisioning_reference"].startswith("mock-grant:sg-sfdc-read")
    assert done["access_expires_at"] is not None

    with _db(app) as db:
        grants = db.query(AccessGrant).all()
        assert len(grants) == 1 and grants[0].mode == "mock" and grants[0].group_id == "sg-sfdc-read"
    notes = api.get("/api/v1/notifications/me", "u-alice").json()
    assert notes[0]["subject"].startswith("Access granted") and "SIMULATED" in notes[0]["body"]
    actions = _actions(api, req["request_number"])
    for expected in ("access_request.submitted", "approval.requested", "approval.approved", "access.provisioned"):
        assert expected in actions
    assert api.get("/api/v1/audit/verify", "u-dave").json()["valid"] is True
    assert api.pending("u-bob") == []


def test_rejected_approval(api, app):
    req = api.submit_access("u-alice").json()
    approval_id = api.pending("u-bob")[0]["approval"]["id"]
    r = api.decide("u-bob", approval_id, "reject", "Use the shared dashboard instead")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    with _db(app) as db:
        assert db.query(AccessGrant).count() == 0
    note = api.get("/api/v1/notifications/me", "u-alice").json()[0]
    assert "rejected" in note["subject"] and "shared dashboard" in note["body"]
    assert "approval.rejected" in _actions(api, req["request_number"])
    # a rejected request can be re-submitted (no longer "active")
    assert api.submit_access("u-alice").status_code == 201


def test_privileged_role_needs_manager_and_application_owner(api):
    req = api.submit_access("u-alice", role="admin", duration_days=14).json()
    assert req["privileged"] is True and req["required_stages"] == 2
    r = api.decide("u-bob", api.pending("u-bob")[0]["approval"]["id"])
    assert r.json()["status"] == "pending_approval" and r.json()["current_stage"] == 2
    owner_pending = api.pending("u-dave")       # Salesforce owner
    assert owner_pending[0]["approval"]["approver_reason"] == "application_owner"
    r = api.decide("u-dave", owner_pending[0]["approval"]["id"])
    assert r.json()["status"] == "provisioned"


def test_owner_requesting_own_app_routes_second_stage_to_governance(api):
    req = api.submit_access("u-dave", role="admin", duration_days=7).json()   # Dave owns Salesforce
    assert req["approvals"][0]["approver_id"] == "u-erin"
    api.decide("u-erin", api.pending("u-erin")[0]["approval"]["id"])
    grace = api.pending("u-grace")
    assert grace and grace[0]["approval"]["approver_reason"] == "governance_fallback"
    assert api.pending("u-dave") == []           # owner can never approve own request


# ------------------------------------------------------------------ security controls
def test_self_approval_prevented_when_directory_says_manager_is_self(api):
    req = api.submit_access("u-frank").json()      # misconfigured: frank.manager_id == frank
    approval = req["approvals"][0]
    assert approval["approver_id"] == "u-grace" and approval["approver_reason"] == "governance_fallback"
    assert "approval.self_approval_prevented" in _actions(api, req["request_number"], "u-frank")
    r = api.decide("u-frank", approval["id"])      # frank has the manager role but is the requester
    assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    assert api.get(f"/api/v1/access-requests/{req['request_number']}", "u-frank").json()["status"] == "pending_approval"


def test_self_approval_blocked_even_if_assigned(api, app):
    """Defence in depth: even if data were tampered so the requester is the approver, the decision is refused."""
    req = api.submit_access("u-bob").json()
    with _db(app) as db:
        appr = db.get(Approval, req["approvals"][0]["id"])
        appr.approver_id, appr.approver_upn = "u-bob", "bob.manager@contoso.example"
        db.commit()
    r = api.decide("u-bob", req["approvals"][0]["id"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    assert "approval.self_approval_blocked" in _actions(api, req["request_number"], "u-bob")


def test_only_assigned_approver_can_decide(api):
    req = api.submit_access("u-alice").json()
    approval_id = req["approvals"][0]["id"]
    r = api.decide("u-erin", approval_id)           # a manager, but not Alice's
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_ASSIGNED_APPROVER"
    r = api.decide("u-carol", approval_id)          # no approver role at all
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    r = api.decide("u-alice", approval_id)          # requester lacks approver role
    assert r.status_code == 403


def test_decision_cannot_be_replayed(api):
    req = api.submit_access("u-alice").json()
    approval_id = req["approvals"][0]["id"]
    assert api.decide("u-bob", approval_id).status_code == 200
    r = api.decide("u-bob", approval_id, "reject")
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_ALREADY_DECIDED"


def test_duplicate_and_idempotent_requests(api, app):
    first = api.submit_access("u-alice", key="idem-key-0001")
    replay = api.submit_access("u-alice", key="idem-key-0001")
    assert first.status_code == replay.status_code == 201
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json()["id"] == first.json()["id"]
    dup = api.submit_access("u-alice", key="idem-key-0002")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_REQUEST"
    reuse = api.submit_access("u-alice", key="idem-key-0001", duration_days=60)
    assert reuse.status_code == 422 and reuse.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    missing = api.post("/api/v1/access-requests", "u-alice", {"application_id": "jira-cloud", "role": "user",
                       "duration_days": 30, "justification": "Need to track sprint work items daily"})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    with _db(app) as db:
        assert db.query(AccessRequest).count() == 1
        assert db.query(Approval).count() == 1


def test_request_validation(api):
    cases = [
        ({"application_id": "domain-admins"}, "UNKNOWN_APPLICATION"),
        ({"role": "superuser"}, "UNKNOWN_ROLE"),
        ({"role": "admin", "duration_days": 90}, "DURATION_EXCEEDS_POLICY"),
        ({"justification": "too short"}, "VALIDATION_FAILED"),
        ({"requester_id": "u-bob"}, "VALIDATION_FAILED"),   # identity comes from the token only
        ({"status": "approved"}, "VALIDATION_FAILED"),     # LLM/caller cannot pre-approve
        ({"duration_days": 0}, "VALIDATION_FAILED"),
    ]
    for override, code in cases:
        r = api.submit_access("u-alice", **override)
        assert r.status_code == 422, (override, r.text)
        assert r.json()["error"]["code"] == code, override


def test_visibility_and_cancel(api):
    req = api.submit_access("u-alice").json()
    ref = req["request_number"]
    assert api.get(f"/api/v1/access-requests/{ref}", "u-bob").status_code == 200    # approver
    assert api.get(f"/api/v1/access-requests/{ref}", "u-carol").status_code == 200  # IT agent
    assert api.get(f"/api/v1/access-requests/{ref}", "u-erin").status_code == 404   # unrelated
    assert api.post(f"/api/v1/access-requests/{ref}/cancel", "u-bob").status_code == 403
    r = api.post(f"/api/v1/access-requests/{ref}/cancel", "u-alice")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert api.decide("u-bob", req["approvals"][0]["id"]).status_code == 409


# ------------------------------------------------------------------ Power Automate callback path
def test_flow_callback_validates_token_and_responder(api, container):
    req = api.submit_access("u-alice").json()
    approval_id = req["approvals"][0]["id"]
    token = container.approvals.issued_tokens[approval_id]
    body = {"approval_id": approval_id, "decision": "approve", "responder_id": "u-bob",
            "responder_upn": "bob.manager@contoso.example", "comment": "ok"}
    r = api.post("/api/v1/approvals/callback", json=body, headers={"X-Callback-Token": "forged"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CALLBACK_TOKEN"
    r = api.post("/api/v1/approvals/callback", json={**body, "responder_id": "u-erin",
                                                     "responder_upn": "erin.director@contoso.example"},
                 headers={"X-Callback-Token": token})
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_ASSIGNED_APPROVER"
    r = api.post("/api/v1/approvals/callback", json={**body, "responder_id": "u-alice",
                                                     "responder_upn": "alice.employee@contoso.example"},
                 headers={"X-Callback-Token": token})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    r = api.post("/api/v1/approvals/callback", json=body, headers={"X-Callback-Token": token})
    assert r.status_code == 200 and r.json()["status"] == "provisioned"
    r = api.post("/api/v1/approvals/callback", json=body, headers={"X-Callback-Token": token})
    assert r.status_code in (401, 409)          # token is single-use


def test_simulated_flow_callback_endpoint(api):
    req = api.submit_access("u-alice").json()
    r = api.post(f"/api/v1/approvals/{req['approvals'][0]['id']}/simulate-flow-callback", "u-bob",
                 {"decision": "reject", "comment": "no"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["approvals"][0]["decision_channel"] == "power_automate"


def test_direct_decisions_disabled_when_power_automate_is_the_provider(api, container):
    req = api.submit_access("u-alice").json()
    container.approvals = PowerAutomateApprovalProvider(
        "https://flow.example", transport=httpx.MockTransport(lambda r: httpx.Response(202)))
    r = api.decide("u-bob", req["approvals"][0]["id"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "DIRECT_DECISION_DISABLED"


# ------------------------------------------------------------------ downstream failures
def test_identity_provider_outage(api, app, container):
    container.faults.set("identity", True)
    r = api.submit_access("u-alice")
    assert r.status_code == 503 and r.json()["error"]["code"] == "IDENTITY_PROVIDER_UNAVAILABLE"
    with _db(app) as db:
        assert db.query(AccessRequest).count() == 0
    container.faults.set("identity", False)
    assert api.submit_access("u-alice").status_code == 201


def test_approval_service_outage_marks_request_failed_and_allows_retry(api, app, container):
    container.faults.set("approval", True)
    r = api.submit_access("u-alice", key="outage-key-001")
    assert r.status_code == 503 and r.json()["error"]["code"] == "APPROVAL_SERVICE_UNAVAILABLE"
    with _db(app) as db:
        failed = db.query(AccessRequest).one()
        assert failed.status == "failed"
    retries = [c for c in api.get("/api/v1/admin/metrics", "u-dave").json()["counters"]["retries"]
               if c["labels"]["operation"] == "approval_start"]
    assert retries and retries[0]["value"] == 2
    container.faults.set("approval", False)
    # 5xx responses are not cached, so the same idempotency key can be retried
    r = api.submit_access("u-alice", key="outage-key-001")
    assert r.status_code == 201 and r.json()["status"] == "pending_approval"


def test_provisioning_failure_creates_ticket_and_admin_can_retry(api, app, container):
    req = api.submit_access("u-alice").json()
    container.faults.set("provisioning", True)
    r = api.decide("u-bob", req["approvals"][0]["id"])
    assert r.status_code == 200 and r.json()["status"] == "provisioning_failed"
    with _db(app) as db:
        ticket = db.query(Ticket).filter(Ticket.source == "access-automation").one()
        assert ticket.requester_id == "u-alice" and ticket.category == "access"
        assert db.query(AccessGrant).count() == 0
    assert "provisioning delayed" in api.get("/api/v1/notifications/me", "u-alice").json()[0]["subject"]
    ref = req["request_number"]
    assert api.post(f"/api/v1/access-requests/{ref}/retry-provisioning", "u-alice").status_code == 403
    container.faults.set("provisioning", False)
    r = api.post(f"/api/v1/access-requests/{ref}/retry-provisioning", "u-dave")
    assert r.status_code == 200 and r.json()["status"] == "provisioned"
    assert "access.provisioning_retry" in _actions(api, ref)


def test_notification_outage_does_not_break_workflow(api, app, container):
    container.faults.set("notification", True)
    req = api.submit_access("u-alice").json()
    r = api.decide("u-bob", req["approvals"][0]["id"])
    assert r.json()["status"] == "provisioned"
    with _db(app) as db:
        assert {n.status for n in db.query(Notification).all()} == {"failed"}


# ------------------------------------------------------------------ expiry
def test_expired_approval_cannot_be_decided(api, app):
    req = api.submit_access("u-alice").json()
    with _db(app) as db:
        appr = db.get(Approval, req["approvals"][0]["id"])
        appr.due_at = utcnow() - timedelta(hours=1)
        db.commit()
    r = api.decide("u-bob", req["approvals"][0]["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_EXPIRED"
    assert api.get(f"/api/v1/access-requests/{req['id']}", "u-alice").json()["status"] == "expired"


def test_expiry_sweep_revokes_expired_grants(api, app):
    req = api.submit_access("u-alice").json()
    api.decide("u-bob", req["approvals"][0]["id"])
    with _db(app) as db:
        grant = db.query(AccessGrant).one()
        grant.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert api.post("/api/v1/admin/maintenance/expire", "u-alice").status_code == 403
    result = api.post("/api/v1/admin/maintenance/expire", "u-dave").json()
    assert result["grants_revoked"] == 1
    assert api.get(f"/api/v1/access-requests/{req['id']}", "u-alice").json()["status"] == "expired"


def test_catalog_lists_roles(api):
    apps = api.get("/api/v1/access/applications", "u-alice").json()
    sf = next(a for a in apps if a["id"] == "salesforce-crm")
    assert {r["name"] for r in sf["roles"]} == {"read_only", "sales_user", "admin"}
    assert "group_id" not in sf["roles"][0]       # internal group mapping is not exposed
