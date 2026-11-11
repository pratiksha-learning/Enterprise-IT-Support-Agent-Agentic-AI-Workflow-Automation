TICKET = {"title": "Laptop screen flickering", "description": "Screen flickers since this morning after update",
          "category": "hardware", "priority": "P3"}


def test_health_endpoints(api):
    assert api.client.get("/health/live").json() == {"status": "ok"}
    ready = api.client.get("/health/ready").json()
    assert ready["status"] == "ready" and ready["adapters"]["provisioning"] == "mock"


def test_unauthenticated_and_invalid_tokens_are_rejected(api):
    r = api.client.get("/api/v1/tickets")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    r = api.client.get("/api/v1/tickets", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    r = api.client.get("/api/v1/tickets", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401
    assert api.client.post("/api/v1/auth/dev-token", json={"user_id": "u-mallory"}).status_code == 401


def test_me_returns_roles_and_permissions(api):
    me = api.get("/api/v1/auth/me", "u-bob").json()
    assert me["id"] == "u-bob" and "approvals:decide" in me["permissions"]


def test_create_get_status_and_list(api):
    r = api.post("/api/v1/tickets", "u-alice", TICKET)
    assert r.status_code == 201
    t = r.json()
    assert t["ticket_number"].startswith("INC") and t["status"] == "new" and t["requester_id"] == "u-alice"
    number = t["ticket_number"]
    assert api.get(f"/api/v1/tickets/{number}", "u-alice").json()["title"] == TICKET["title"]
    status = api.get(f"/api/v1/tickets/{number}/status", "u-alice").json()
    assert status == {"ticket_number": number, "status": "new", "priority": "P3", "updated_at": status["updated_at"]}
    assert api.get("/api/v1/tickets", "u-alice").json()["total"] == 1
    api.post("/api/v1/tickets", "u-bob", {**TICKET, "title": "Bob's VPN issue"})
    assert api.get("/api/v1/tickets", "u-alice").json()["total"] == 1       # own only
    assert api.get("/api/v1/tickets", "u-carol").json()["total"] == 2       # IT agent sees all


def test_ticket_validation_errors(api):
    r = api.post("/api/v1/tickets", "u-alice", {**TICKET, "title": "x"})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == "title"
    r = api.post("/api/v1/tickets", "u-alice", {**TICKET, "priority": "P9"})
    assert r.status_code == 422
    # callers cannot set server-controlled fields (requester spoofing / status injection)
    r = api.post("/api/v1/tickets", "u-alice", {**TICKET, "requester_id": "u-bob"})
    assert r.status_code == 422
    r = api.post("/api/v1/tickets", "u-alice", {**TICKET, "status": "resolved"})
    assert r.status_code == 422
    assert api.get("/api/v1/tickets/NOTATICKET", "u-alice").status_code == 422


def test_other_users_ticket_is_not_found(api):
    number = api.post("/api/v1/tickets", "u-alice", TICKET).json()["ticket_number"]
    r = api.get(f"/api/v1/tickets/{number}", "u-bob")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TICKET_NOT_FOUND"
    assert api.get("/api/v1/tickets/INC9999999", "u-alice").status_code == 404


def test_status_update_authorisation_and_transitions(api):
    number = api.post("/api/v1/tickets", "u-alice", TICKET).json()["ticket_number"]
    r = api.patch(f"/api/v1/tickets/{number}/status", "u-alice", {"status": "resolved"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TICKET_UPDATE_FORBIDDEN"
    r = api.patch(f"/api/v1/tickets/{number}/status", "u-carol", {"status": "in_progress", "comment": "Looking"})
    assert r.status_code == 200 and r.json()["status"] == "in_progress" and r.json()["assigned_to"] == "u-carol"
    r = api.patch(f"/api/v1/tickets/{number}/status", "u-carol", {"status": "closed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert api.patch(f"/api/v1/tickets/{number}/status", "u-carol", {"status": "resolved"}).status_code == 200
    assert api.patch(f"/api/v1/tickets/{number}/status", "u-carol", {"status": "closed"}).status_code == 200
    history = api.get(f"/api/v1/tickets/{number}/history", "u-alice").json()
    assert [h["to_status"] for h in history] == ["new", "in_progress", "resolved", "closed"]


def test_requester_can_cancel_own_ticket(api):
    number = api.post("/api/v1/tickets", "u-alice", TICKET).json()["ticket_number"]
    r = api.patch(f"/api/v1/tickets/{number}/status", "u-alice", {"status": "cancelled"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_ticket_idempotency(api):
    h = {"Idempotency-Key": "ticket-key-0001"}
    first = api.post("/api/v1/tickets", "u-alice", TICKET, headers=h)
    second = api.post("/api/v1/tickets", "u-alice", TICKET, headers=h)
    assert first.status_code == second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["ticket_number"] == second.json()["ticket_number"]
    assert api.get("/api/v1/tickets", "u-alice").json()["total"] == 1
    r = api.post("/api/v1/tickets", "u-alice", {**TICKET, "title": "Different title"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    r = api.post("/api/v1/tickets", "u-alice", TICKET, headers={"Idempotency-Key": "bad key!"})
    assert r.status_code == 422


def test_audit_endpoints_require_permission_and_chain_is_valid(api):
    api.post("/api/v1/tickets", "u-alice", TICKET)
    assert api.get("/api/v1/audit", "u-alice").status_code == 403
    entries = api.get("/api/v1/audit", "u-carol").json()
    assert entries["total"] >= 1 and entries["items"][0]["action"] == "ticket.created"
    assert api.get("/api/v1/audit/verify", "u-dave").json()["valid"] is True
