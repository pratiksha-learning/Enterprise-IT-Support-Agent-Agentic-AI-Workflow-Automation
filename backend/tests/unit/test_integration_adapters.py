"""Adapters for real Microsoft services, verified against httpx.MockTransport (no tenant needed)."""
import json

import httpx
import pytest

from app.integrations.approvals import ApprovalTask, PowerAutomateApprovalProvider
from app.integrations.common import FaultInjector, IntegrationError, with_retry
from app.integrations.graph_client import GraphClient
from app.integrations.identity import GraphIdentityProvider
from app.integrations.notifications import TeamsFlowNotificationProvider
from app.integrations.provisioning import EntraGroupProvisioningProvider, MockProvisioningProvider
from app.integrations.sharepoint import SharePointGraphSource
from app.observability.telemetry import metrics_registry


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        if "oauth2/v2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "graph-token", "expires_in": 3600})
        return self.handler(request)


def graph(handler) -> tuple[GraphClient, Recorder]:
    rec = Recorder(handler)
    return GraphClient("tenant", "client", "secret", transport=httpx.MockTransport(rec)), rec


def test_with_retry_retries_transient_then_succeeds():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise IntegrationError("boom")
        return "ok"

    assert with_retry("op", flaky, attempts=3, backoff=0, sleep=lambda _: None) == "ok"
    retries = metrics_registry.snapshot()["counters"]["retries"]
    assert retries[0]["value"] == 2


def test_with_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise IntegrationError("400", transient=False)

    with pytest.raises(IntegrationError):
        with_retry("op", bad, attempts=3, backoff=0, sleep=lambda _: None)
    assert calls["n"] == 1


def test_graph_token_is_cached_and_sent():
    client, rec = graph(lambda r: httpx.Response(200, json={"id": "u1", "userPrincipalName": "a@x", "displayName": "A"}))
    provider = GraphIdentityProvider(client)
    provider.get_user("u1")
    provider.get_user("u1")
    token_calls = [c for c in rec.calls if "token" in str(c.url)]
    api_calls = [c for c in rec.calls if "graph.microsoft.com" in str(c.url)]
    assert len(token_calls) == 1 and len(api_calls) == 2
    assert api_calls[0].headers["Authorization"] == "Bearer graph-token"


def test_graph_manager_404_means_no_manager_and_429_is_transient():
    client, _ = graph(lambda r: httpx.Response(404, json={}))
    assert GraphIdentityProvider(client).get_manager("u1") is None
    client, _ = graph(lambda r: httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(IntegrationError) as exc:
        GraphIdentityProvider(client).get_manager("u1")
    assert exc.value.transient


def test_entra_group_provisioning_request_shape_and_idempotency():
    client, rec = graph(lambda r: httpx.Response(204))
    p = EntraGroupProvisioningProvider(client, {"sg-1"})
    result = p.grant("user-1", "sg-1")
    call = [c for c in rec.calls if "groups" in str(c.url)][0]
    assert call.method == "POST" and str(call.url).endswith("/groups/sg-1/members/$ref")
    assert json.loads(call.content)["@odata.id"].endswith("/directoryObjects/user-1")
    assert result.simulated is False
    # already a member -> Graph 400 -> treated as success
    client, _ = graph(lambda r: httpx.Response(400, json={"error": {"message": "already exist"}}))
    EntraGroupProvisioningProvider(client, {"sg-1"}).grant("user-1", "sg-1")


def test_entra_group_provisioning_refuses_groups_outside_catalog():
    client, rec = graph(lambda r: httpx.Response(204))
    with pytest.raises(IntegrationError):
        EntraGroupProvisioningProvider(client, {"sg-1"}).grant("user-1", "domain-admins")
    assert not [c for c in rec.calls if "groups" in str(c.url)]


def test_mock_provisioning_is_simulated_and_fault_injectable():
    faults = FaultInjector()
    p = MockProvisioningProvider(faults)
    assert p.grant("u", "g").simulated is True
    faults.set("provisioning", True)
    with pytest.raises(IntegrationError):
        p.grant("u", "g")


def _task():
    return ApprovalTask("a" * 32, "r1", "ACC100001", 1, "alice@x", "Alice", "u-bob", "bob@x", "Salesforce", "read_only",
                        False, 30, "justification text long enough", "2026-01-01T00:00:00+00:00",
                        "http://localhost/api/v1/approvals/callback", "one-time-token")


def test_power_automate_approval_payload_and_errors():
    seen = {}

    def ok(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, headers={"x-ms-workflow-run-id": "run-42"})

    provider = PowerAutomateApprovalProvider("https://flow.example/trigger?sig=s", transport=httpx.MockTransport(ok))
    assert provider.start(_task()) == "run-42"
    assert seen["body"]["approverUpn"] == "bob@x" and seen["body"]["callbackToken"] == "one-time-token"
    failing = PowerAutomateApprovalProvider("https://flow.example", transport=httpx.MockTransport(
        lambda r: httpx.Response(502)))
    with pytest.raises(IntegrationError) as exc:
        failing.start(_task())
    assert exc.value.transient


def test_teams_notification_flow_errors_are_surfaced():
    provider = TeamsFlowNotificationProvider("https://flow.example", transport=httpx.MockTransport(
        lambda r: httpx.Response(500)))
    with pytest.raises(IntegrationError):
        provider.send("a@x", "s", "b")


def test_sharepoint_source_maps_permissions_and_skips_restricted_items():
    def handler(request: httpx.Request):
        url = str(request.url)
        if url.endswith("/root/children"):
            return httpx.Response(200, json={"value": [
                {"id": "f1", "name": "Folder", "folder": {}},
                {"id": "d1", "name": "vpn.md", "file": {}, "webUrl": "https://sp/vpn.md"},
                {"id": "d2", "name": "secret.md", "file": {}, "webUrl": "https://sp/secret.md"},
                {"id": "d3", "name": "nogroups.md", "file": {}, "webUrl": "https://sp/nogroups.md"},
                {"id": "d4", "name": "image.png", "file": {}}]})
        if url.endswith("/items/f1/children"):
            return httpx.Response(200, json={"value": [{"id": "d5", "name": "mfa.txt", "file": {}, "webUrl": "https://sp/mfa.txt"}]})
        if url.endswith("extractSensitivityLabels"):
            label = "Highly Confidential" if "/d2/" in url else "General"
            return httpx.Response(200, json={"labels": [{"name": label}]})
        if url.endswith("/permissions"):
            if "/d3/" in url:
                return httpx.Response(200, json={"value": []})
            return httpx.Response(200, json={"value": [{"grantedToV2": {"group": {"id": "grp-all"}}}]})
        if url.endswith("/content"):
            return httpx.Response(200, content=b"# Doc\n\nInstall the VPN client.")
        return httpx.Response(404)

    client, _ = graph(handler)
    docs = list(SharePointGraphSource(client, "site", "drive").iter_documents())
    names = sorted(d.source_uri for d in docs)
    assert names == ["https://sp/mfa.txt", "https://sp/vpn.md"]   # labelled + ACL-less docs excluded (fail closed)
    assert all(d.allowed_groups == ["grp-all"] and d.source == "sharepoint" for d in docs)
