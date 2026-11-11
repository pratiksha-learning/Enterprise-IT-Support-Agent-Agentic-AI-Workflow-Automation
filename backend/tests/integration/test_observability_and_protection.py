from fastapi.testclient import TestClient

from app.models.orm import AuditLog


def test_correlation_id_is_generated_or_propagated(api):
    r = api.client.get("/health/live")
    assert len(r.headers["X-Correlation-ID"]) == 32
    r = api.client.get("/health/live", headers={"X-Correlation-ID": "abc-123-correlation"})
    assert r.headers["X-Correlation-ID"] == "abc-123-correlation"
    r = api.client.get("/health/live", headers={"X-Correlation-ID": "bad\nvalue"})   # log-injection attempt
    assert r.headers["X-Correlation-ID"] != "bad\nvalue"


def test_correlation_id_lands_in_error_body_and_audit(api, app):
    cid = "trace-test-0000001"
    r = api.client.post("/api/v1/tickets", json={"title": "Monitor broken", "description": "External monitor shows no signal",
                                                 "category": "hardware", "priority": "P3"},
                        headers={**api.h("u-alice"), "X-Correlation-ID": cid})
    assert r.status_code == 201
    with app.state.session_factory() as db:
        assert db.query(AuditLog).filter(AuditLog.correlation_id == cid).count() == 1
    r = api.client.get("/api/v1/tickets/INC9999999", headers={**api.h("u-alice"), "X-Correlation-ID": cid})
    assert r.json()["error"]["correlation_id"] == cid


def test_metrics_dashboard_is_admin_only_and_records_tools(api):
    api.post("/api/v1/agent/chat", "u-alice", {"message": "How do I reset my password?"})
    assert api.get("/api/v1/admin/metrics", "u-alice").status_code == 403
    m = api.get("/api/v1/admin/metrics", "u-dave").json()
    tools = {c["labels"]["tool"]: c["value"] for c in m["counters"]["tool_invocations"]}
    assert tools["search_it_knowledge"] == 1
    assert m["counters"]["agent_executions"][0]["labels"]["intent"] == "knowledge"
    assert any(h["labels"]["route"] == "POST /api/v1/agent/chat" for h in m["histograms"]["http_latency_ms"])


def test_approval_metrics(api):
    req = api.submit_access("u-alice").json()
    api.decide("u-bob", req["approvals"][0]["id"])
    m = api.get("/api/v1/admin/metrics", "u-dave").json()
    assert m["counters"]["approvals_completed"][0]["labels"]["outcome"] == "approved"
    assert m["histograms"]["approval_duration_s"][0]["count"] == 1


def test_audit_details_are_redacted(api, app):
    api.submit_access("u-alice", justification="Need it for reporting, my token: abcdefghijklmnop123 thanks")
    with app.state.session_factory() as db:
        assert "abcdefghijklmnop123" not in str([e.details for e in db.query(AuditLog).all()])


def test_audit_chain_detects_tampering(api, app):
    api.submit_access("u-alice")
    assert api.get("/api/v1/audit/verify", "u-dave").json()["valid"] is True
    with app.state.session_factory() as db:
        entry = db.query(AuditLog).order_by(AuditLog.id).first()
        entry.actor_id = "u-mallory"
        db.commit()
    result = api.get("/api/v1/audit/verify", "u-dave").json()
    assert result["valid"] is False and result["first_invalid_id"] == entry.id


def test_rate_limiting_returns_429_with_retry_after(app_factory):
    client = TestClient(app_factory(rate_limit_enabled=True, rate_limit_burst=5, rate_limit_per_minute=60))
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "u-alice"}).json()["access_token"]
    statuses = [client.get("/api/v1/tickets", headers={"Authorization": f"Bearer {token}"}).status_code
                for _ in range(8)]
    assert 429 in statuses and statuses[0] == 200
    r = client.get("/api/v1/tickets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert client.get("/health/live").status_code == 200      # health is exempt


def test_fault_injection_admin_only(api):
    assert api.post("/api/v1/admin/fault-injection", "u-alice", {"component": "approval", "enabled": True}).status_code == 403
    r = api.post("/api/v1/admin/fault-injection", "u-dave", {"component": "approval", "enabled": True})
    assert r.status_code == 200 and r.json()["approval"] is True
    assert api.post("/api/v1/admin/fault-injection", "u-dave", {"component": "database", "enabled": True}).status_code == 422


def test_unknown_route_returns_structured_404(api):
    r = api.client.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_openapi_schema_available(api):
    spec = api.client.get("/openapi.json").json()
    assert "/api/v1/access-requests" in spec["paths"] and "/api/v1/tickets" in spec["paths"]
