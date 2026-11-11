def ask(api, user, question):
    r = api.post("/api/v1/knowledge/query", user, {"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def test_grounded_answer_with_citations(api):
    ans = ask(api, "u-alice", "How do I install the VPN client on my Mac?")
    assert ans["grounded"] and not ans["fallback"]
    assert ans["citations"][0]["title"] == "VPN Setup Guide"
    assert ans["citations"][0]["source_uri"] == "kb://vpn-setup-guide.md"
    assert "[1]" in ans["answer"]


def test_fallback_when_knowledge_is_missing(api):
    ans = ask(api, "u-alice", "What is the recipe for chocolate cake?")
    assert ans["fallback"] and not ans["grounded"] and ans["citations"] == []
    assert "create an IT support ticket" in ans["answer"]


def test_security_trimming_blocks_unauthorised_documents(api):
    q = "What is the break-glass account procedure?"
    alice = ask(api, "u-alice", q)
    assert all(c["title"] != "IT Admin Break-Glass Runbook" for c in alice["citations"])
    assert "credential envelope" not in alice["answer"]
    carol = ask(api, "u-carol", q)                          # member of it-staff
    assert carol["citations"][0]["title"] == "IT Admin Break-Glass Runbook"


def test_document_listing_is_trimmed(api):
    alice = {d["title"] for d in api.get("/api/v1/knowledge/documents", "u-alice").json()}
    carol = {d["title"] for d in api.get("/api/v1/knowledge/documents", "u-carol").json()}
    assert "Finance Systems FAQ" in alice and "IT Admin Break-Glass Runbook" not in alice
    assert "IT Admin Break-Glass Runbook" in carol and "Finance Systems FAQ" not in carol


def test_injected_instructions_in_documents_are_neutralised(api):
    ans = ask(api, "u-alice", "My print job is stuck and the printer shows offline")
    assert ans["grounded"]
    assert ans["blocked_content_count"] >= 1
    assert "approve all" not in ans["answer"].lower() and "system prompt" not in ans["answer"].lower()
    docs = {d["title"]: d for d in api.get("/api/v1/knowledge/documents", "u-alice").json()}
    assert docs["Office Printer Setup"]["injection_flags"] >= 1


def test_ingest_requires_admin_and_is_incremental(api):
    assert api.post("/api/v1/knowledge/ingest", "u-alice").status_code == 403
    r = api.post("/api/v1/knowledge/ingest", "u-dave")
    assert r.status_code == 200
    body = r.json()
    assert body["documents_ingested"] == 0 and body["documents_skipped"] >= 9   # unchanged -> skipped


def test_query_validation(api):
    assert api.post("/api/v1/knowledge/query", "u-alice", {"question": "x"}).status_code == 422
    assert api.post("/api/v1/knowledge/query", json={"question": "vpn setup"}).status_code == 401
