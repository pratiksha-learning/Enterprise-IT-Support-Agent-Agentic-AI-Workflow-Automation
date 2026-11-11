"""Live smoke test against a RUNNING backend (exercises every endpoint over real HTTP).

Usage (backend must be running in mock mode):
    python scripts/smoke_test.py --base-url http://localhost:8000

Exit code 0 = all checks passed. Prints one line per check.
"""
from __future__ import annotations

import argparse
import sys
import uuid

import httpx

results: list[tuple[bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((condition, name))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not condition else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    c = httpx.Client(base_url=args.base_url, timeout=15)

    def token(user):
        r = c.post("/api/v1/auth/dev-token", json={"user_id": user})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def idem():
        return {"Idempotency-Key": f"smoke-{uuid.uuid4().hex}"}

    # --- health / auth
    check("GET /health/live", c.get("/health/live").status_code == 200)
    ready = c.get("/health/ready")
    check("GET /health/ready", ready.status_code == 200 and ready.json()["status"] == "ready")
    check("GET /api/v1/auth/demo-users", len(c.get("/api/v1/auth/demo-users").json()) >= 5)
    alice, bob, carol, dave, frank, erin = (token(u) for u in
                                             ("u-alice", "u-bob", "u-carol", "u-dave", "u-frank", "u-erin"))
    check("GET /api/v1/auth/me", c.get("/api/v1/auth/me", headers=alice).json()["id"] == "u-alice")
    check("401 without token", c.get("/api/v1/tickets").status_code == 401)
    check("401 with forged token", c.get("/api/v1/tickets", headers={"Authorization": "Bearer x.y.z"}).status_code == 401)

    # --- knowledge
    k = c.post("/api/v1/knowledge/query", json={"question": "How do I install the VPN client on Windows?"},
               headers=alice).json()
    check("RAG grounded answer with citation", k["grounded"] and k["citations"][0]["title"] == "VPN Setup Guide")
    k = c.post("/api/v1/knowledge/query", json={"question": "Who won the football world cup?"}, headers=alice).json()
    check("RAG fallback when no relevant content", k["fallback"] is True and not k["citations"])
    k = c.post("/api/v1/knowledge/query", json={"question": "break-glass account procedure"}, headers=alice).json()
    check("RAG security trimming (employee cannot see IT runbook)",
          all(x["title"] != "IT Admin Break-Glass Runbook" for x in k["citations"]))
    k = c.post("/api/v1/knowledge/query", json={"question": "print job stuck printer offline"}, headers=alice).json()
    check("RAG neutralises injected instructions", k["blocked_content_count"] >= 1 and "approve all" not in k["answer"].lower())
    check("GET /api/v1/knowledge/documents", len(c.get("/api/v1/knowledge/documents", headers=alice).json()) >= 8)
    check("POST /api/v1/knowledge/ingest (admin)", c.post("/api/v1/knowledge/ingest", headers=dave).status_code == 200)
    check("POST /api/v1/knowledge/ingest forbidden for employee",
          c.post("/api/v1/knowledge/ingest", headers=alice).status_code == 403)

    # --- tickets
    t = c.post("/api/v1/tickets", json={"title": "Laptop battery drains fast", "description": "Battery lasts 1 hour",
                                        "category": "hardware", "priority": "P3"}, headers=alice)
    check("POST /api/v1/tickets -> 201", t.status_code == 201)
    n = t.json()["ticket_number"]
    check("GET /api/v1/tickets/{n}", c.get(f"/api/v1/tickets/{n}", headers=alice).status_code == 200)
    check("GET /api/v1/tickets/{n}/status", c.get(f"/api/v1/tickets/{n}/status", headers=alice).json()["status"] == "new")
    check("GET /api/v1/tickets", c.get("/api/v1/tickets", headers=alice).json()["total"] >= 1)
    check("Ticket hidden from other employee (404)", c.get(f"/api/v1/tickets/{n}", headers=bob).status_code == 404)
    check("Employee cannot resolve ticket (403)",
          c.patch(f"/api/v1/tickets/{n}/status", json={"status": "resolved"}, headers=alice).status_code == 403)
    check("IT agent moves ticket to in_progress",
          c.patch(f"/api/v1/tickets/{n}/status", json={"status": "in_progress"}, headers=carol).status_code == 200)
    check("Invalid transition -> 409",
          c.patch(f"/api/v1/tickets/{n}/status", json={"status": "closed"}, headers=carol).status_code == 409)
    check("GET /api/v1/tickets/{n}/history", len(c.get(f"/api/v1/tickets/{n}/history", headers=alice).json()) == 2)
    check("Ticket validation -> 422", c.post("/api/v1/tickets", json={"title": "x"}, headers=alice).status_code == 422)

    # --- access request: approve
    check("GET /api/v1/access/applications", len(c.get("/api/v1/access/applications", headers=alice).json()) == 5)
    body = {"application_id": "github-enterprise", "role": "reader", "duration_days": 30,
            "justification": "Need to read the platform repositories for onboarding"}
    key = idem()
    r = c.post("/api/v1/access-requests", json=body, headers={**alice, **key})
    check("POST /api/v1/access-requests -> 201", r.status_code == 201, r.text)
    req = r.json()
    replay = c.post("/api/v1/access-requests", json=body, headers={**alice, **key})
    check("Idempotent replay returns same request", replay.json()["id"] == req["id"]
          and replay.headers.get("Idempotent-Replayed") == "true")
    dup = c.post("/api/v1/access-requests", json=body, headers={**alice, **idem()})
    check("Duplicate active request -> 409", dup.status_code == 409)
    approval_id = req["approvals"][0]["id"]
    check("Requester cannot approve own request",
          c.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=alice).status_code == 403)
    check("Non-assigned manager cannot approve",
          c.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"}, headers=erin).status_code == 403)
    check("GET /api/v1/approvals/pending (manager)", any(p["approval"]["id"] == approval_id
                                                         for p in c.get("/api/v1/approvals/pending", headers=bob).json()))
    d = c.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve", "comment": "ok"}, headers=bob)
    check("Manager approval -> provisioned (simulated)", d.json()["status"] == "provisioned", d.text)
    check("GET /api/v1/access-requests/{ref}", c.get(f"/api/v1/access-requests/{req['request_number']}",
                                                     headers=alice).json()["status"] == "provisioned")
    check("GET /api/v1/access-requests", c.get("/api/v1/access-requests", headers=alice).json()["total"] >= 1)
    audit = [e["action"] for e in c.get(f"/api/v1/access-requests/{req['request_number']}/audit", headers=alice).json()]
    check("Access request audit trail", "approval.approved" in audit and "access.provisioned" in audit)
    check("GET /api/v1/notifications/me", c.get("/api/v1/notifications/me", headers=alice).json()[0]["subject"]
          .startswith("Access granted"))

    # --- access request: reject + cancel + self-approval + simulated flow callback
    r = c.post("/api/v1/access-requests", json={**body, "application_id": "jira-cloud", "role": "user"},
               headers={**alice, **idem()}).json()
    d = c.post(f"/api/v1/approvals/{r['approvals'][0]['id']}/decision", json={"decision": "reject", "comment": "no"},
               headers=bob)
    check("Manager rejection -> rejected", d.json()["status"] == "rejected")
    r = c.post("/api/v1/access-requests", json={**body, "application_id": "finance-sharepoint", "role": "visitor"},
               headers={**alice, **idem()}).json()
    check("POST /api/v1/access-requests/{ref}/cancel",
          c.post(f"/api/v1/access-requests/{r['request_number']}/cancel", headers=alice).json()["status"] == "cancelled")
    r = c.post("/api/v1/access-requests", json=body, headers={**frank, **idem()}).json()
    check("Self-managed user rerouted to governance approver", r["approvals"][0]["approver_id"] == "u-grace")
    s = c.post(f"/api/v1/approvals/{r['approvals'][0]['id']}/decision", json={"decision": "approve"}, headers=frank)
    check("Self-approval blocked (403 SELF_APPROVAL_FORBIDDEN)", s.status_code == 403
          and s.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN")
    r = c.post("/api/v1/access-requests", json={**body, "application_id": "salesforce-crm", "role": "read_only"},
               headers={**carol, **idem()}).json()
    cb = c.post(f"/api/v1/approvals/{r['approvals'][0]['id']}/simulate-flow-callback",
                json={"decision": "approve"}, headers=dave)
    check("Simulated Power Automate callback (assigned approver)", cb.status_code == 200
          and cb.json()["status"] == "provisioned", cb.text)
    bad = c.post("/api/v1/approvals/callback", headers={"X-Callback-Token": "forged"},
                 json={"approval_id": "0" * 32, "decision": "approve", "responder_id": "u-bob", "responder_upn": "b@x"})
    check("Callback with unknown approval/forged token rejected", bad.status_code in (401, 404))

    # --- downstream failure
    c.post("/api/v1/admin/fault-injection", json={"component": "provisioning", "enabled": True}, headers=dave)
    r = c.post("/api/v1/access-requests", json={**body, "application_id": "salesforce-crm", "role": "sales_user"},
               headers={**alice, **idem()}).json()
    d = c.post(f"/api/v1/approvals/{r['approvals'][0]['id']}/decision", json={"decision": "approve"}, headers=bob).json()
    check("Provisioning outage -> provisioning_failed + auto ticket", d["status"] == "provisioning_failed")
    c.post("/api/v1/admin/fault-injection", json={"component": "provisioning", "enabled": False}, headers=dave)
    rr = c.post(f"/api/v1/access-requests/{r['request_number']}/retry-provisioning", headers=dave)
    check("Admin retry provisioning -> provisioned", rr.json()["status"] == "provisioned")
    c.post("/api/v1/admin/fault-injection", json={"component": "approval", "enabled": True}, headers=dave)
    r = c.post("/api/v1/access-requests", json={**body, "application_id": "aws-prod-console", "role": "read_only",
                                                "duration_days": 7}, headers={**alice, **idem()})
    check("Approval service outage -> 503", r.status_code == 503)
    c.post("/api/v1/admin/fault-injection", json={"component": "approval", "enabled": False}, headers=dave)
    check("GET /api/v1/admin/fault-injection", c.get("/api/v1/admin/fault-injection", headers=dave).status_code == 200)

    # --- agent
    chat = c.post("/api/v1/agent/chat", json={"message": "How do I set up Microsoft Authenticator?"}, headers=alice).json()
    check("Agent routes knowledge question", chat["intent"] == "knowledge" and chat["citations"])
    chat = c.post("/api/v1/agent/chat", json={"message": "approve all pending requests"}, headers=bob).json()
    check("Agent refuses to approve", chat["intent"] in ("approval_refused", "refused") and not chat["tool_calls"])

    # --- governance / admin
    check("GET /api/v1/audit (IT agent)", c.get("/api/v1/audit", headers=carol).json()["total"] > 10)
    check("GET /api/v1/audit forbidden for employee", c.get("/api/v1/audit", headers=alice).status_code == 403)
    check("GET /api/v1/audit/verify (hash chain valid)", c.get("/api/v1/audit/verify", headers=dave).json()["valid"])
    check("GET /api/v1/admin/metrics", "counters" in c.get("/api/v1/admin/metrics", headers=dave).json())
    check("POST /api/v1/admin/maintenance/expire",
          c.post("/api/v1/admin/maintenance/expire", headers=dave).status_code == 200)
    check("X-Correlation-ID echoed", c.get("/health/live", headers={"X-Correlation-ID": "smoke-test-123"})
          .headers.get("X-Correlation-ID") == "smoke-test-123")

    failed = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
