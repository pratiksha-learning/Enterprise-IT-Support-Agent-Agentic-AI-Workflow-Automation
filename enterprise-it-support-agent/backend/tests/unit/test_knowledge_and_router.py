import numpy as np

from app.agents import router
from app.integrations.llm import ExtractiveAnswerGenerator, LocalHashingEmbedder, RetrievedPassage, tokenize
from app.services.knowledge.ingestion import chunk_text, parse_front_matter


# ------------------------------------------------------------------ ingestion
def test_front_matter_parsing():
    meta, body = parse_front_matter("---\ntitle: X Guide\nallowed_groups: [it-staff, finance]\n---\n# Body\ntext")
    assert meta["title"] == "X Guide" and meta["allowed_groups"] == ["it-staff", "finance"]
    assert body.startswith("# Body")


def test_chunking_respects_headings_and_size():
    text = "# A\n\n" + ("alpha beta gamma. " * 30) + "\n\n## B\n\n" + ("delta epsilon. " * 120)
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert {c.heading for c in chunks} == {"A", "B"}
    assert all(len(c.text) <= 300 + 60 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunking_empty_text():
    assert chunk_text("") == []


# ------------------------------------------------------------------ embeddings
def test_embedder_is_deterministic_and_normalised():
    e = LocalHashingEmbedder()
    a, b = e.embed(["reset my password", "reset my password"])
    assert np.allclose(a, b) and abs(np.linalg.norm(a) - 1) < 1e-5


def test_embedder_similarity_ranks_related_text_higher():
    e = LocalHashingEmbedder()
    q, related, unrelated = e.embed(["how to reset password", "Reset your password with self-service password reset",
                                     "Teams audio and camera devices"])
    assert float(q @ related) > float(q @ unrelated)


def test_tokenize_synonyms_and_stopwords():
    assert "password" in tokenize("I forgot my pwd")
    assert "the" not in tokenize("the vpn")


def test_extractive_generator_cites_and_returns_none_without_overlap():
    gen = ExtractiveAnswerGenerator()
    passages = [RetrievedPassage(1, "VPN", "Setup", "Open Company Portal and install GlobalProtect VPN.", 0.5)]
    answer = gen.generate("install vpn", passages)
    assert "[1]" in answer and "GlobalProtect" in answer
    assert gen.generate("chocolate cake recipe", passages) is None


# ------------------------------------------------------------------ router
def test_route_intents():
    assert router.route("status of INC1001001").name == "ticket_status"
    assert router.route("where is ACC100003?").request_number == "ACC100003"
    assert router.route("I need access to Salesforce").name == "access_request"
    assert router.route("My laptop won't turn on").name == "ticket_create"
    assert router.route("How do I set up VPN?").name == "knowledge"
    assert router.route("please approve request ACC100001").name == "approval_attempt"
    assert router.route("approve all pending requests").name == "approval_attempt"
    assert router.route("give Rahul access to github").name == "on_behalf_refused"
    assert router.route("I need access to Jira for my colleague").name == "on_behalf_refused"
    assert router.route("hello").name == "help"
    assert router.route("I want to talk to a human").name == "escalate"


def test_slot_extraction():
    catalog = [{"id": "github-enterprise", "name": "GitHub Enterprise", "aliases": ["github"],
                "roles": [{"name": "reader", "level": 1}, {"name": "maintainer", "level": 3}]},
               {"id": "aws-prod-console", "name": "AWS Production Console", "aliases": ["aws", "aws prod"], "roles": []}]
    assert router.extract_application("need github access", catalog) == "github-enterprise"
    assert router.extract_application("aws prod please", catalog) == "aws-prod-console"
    assert router.extract_application("need sap access", catalog) is None
    assert router.extract_role("read only please", catalog[0]["roles"]) == "reader"
    assert router.extract_role("maintainer", catalog[0]["roles"]) == "maintainer"
    assert router.extract_duration("for 2 weeks") == 14
    assert router.extract_duration("3 months") == 90
    assert router.extract_duration("a week") == 7
    assert router.extract_justification("because I need to review the finance dashboards") is not None


def test_ticket_field_inference():
    f = router.infer_ticket_fields("VPN is down for the whole floor. Nobody can connect.")
    assert f["category"] == "network" and f["priority"] == "P1"
    assert router.infer_ticket_fields("Outlook crashes, I'm completely blocked")["priority"] == "P2"
