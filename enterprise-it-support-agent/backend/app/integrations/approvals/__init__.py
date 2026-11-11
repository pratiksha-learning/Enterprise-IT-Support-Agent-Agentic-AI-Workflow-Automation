"""Approval-provider adapters.

The provider only *starts* an approval. Decisions always come back to the backend
(mock decision endpoint or signed Power Automate callback) where they are validated -
the provider and the LLM are never trusted to change request state directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.integrations.common import FaultInjector, IntegrationError


@dataclass
class ApprovalTask:
    approval_id: str
    access_request_id: str
    request_number: str
    stage: int
    requester_upn: str
    requester_display_name: str
    approver_id: str
    approver_upn: str
    application_name: str
    role: str
    privileged: bool
    duration_days: int
    justification: str
    due_at_iso: str
    callback_url: str
    callback_token: str


class ApprovalProvider(Protocol):
    name: str
    supports_direct_decisions: bool

    def start(self, task: ApprovalTask) -> str: ...


class MockApprovalProvider:
    """Local approval service: the pending approval lives in the database and the manager
    decides in the demo UI (``POST /api/v1/approvals/{id}/decision``) as an authenticated user."""
    name = "mock"
    supports_direct_decisions = True

    def __init__(self, faults: FaultInjector):
        self._faults = faults
        # Mock-only: lets the demo/tests simulate the Power Automate callback. Never used in production.
        self.issued_tokens: dict[str, str] = {}

    def start(self, task: ApprovalTask) -> str:
        self._faults.check("approval")
        return f"mock-approval:{task.approval_id}"


class PowerAutomateApprovalProvider:
    """Triggers the 'IT Access Request Approval' flow (HTTP request trigger).

    The flow runs *Start and wait for an approval* assigned to ``approver_upn`` and posts the
    outcome to ``callback_url`` with the one-time ``callback_token``.
    """
    name = "power_automate"
    supports_direct_decisions = False

    def __init__(self, flow_url: str, transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        self._url = flow_url
        self._http = httpx.Client(transport=transport, timeout=timeout)

    def start(self, task: ApprovalTask) -> str:
        payload = {
            "approvalId": task.approval_id, "accessRequestId": task.access_request_id,
            "requestNumber": task.request_number, "stage": task.stage,
            "requesterUpn": task.requester_upn, "requesterDisplayName": task.requester_display_name,
            "approverUpn": task.approver_upn, "approverId": task.approver_id,
            "applicationName": task.application_name, "role": task.role, "privileged": task.privileged,
            "durationDays": task.duration_days, "justification": task.justification,
            "dueAt": task.due_at_iso, "callbackUrl": task.callback_url, "callbackToken": task.callback_token,
        }
        try:
            resp = self._http.post(self._url, json=payload)
        except httpx.TransportError as exc:
            raise IntegrationError(f"approval flow unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise IntegrationError(f"approval flow returned {resp.status_code}", status=resp.status_code)
        if resp.status_code >= 400:
            raise IntegrationError(f"approval flow rejected request ({resp.status_code})", transient=False,
                                   status=resp.status_code)
        return resp.headers.get("x-ms-workflow-run-id", "power-automate-run")
