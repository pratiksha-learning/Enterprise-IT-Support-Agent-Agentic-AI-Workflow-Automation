"""Local agent orchestrator (Copilot Studio emulator for the demo UI).

Responsibilities: guardrails -> intent routing -> slot filling -> confirmation -> tool call
-> grounded reply. It holds *no* authority of its own: every action runs as the signed-in
user through ``invoke_tool`` and the service layer re-checks everything.
"""
from __future__ import annotations

import hashlib
import json
import time

from sqlalchemy.orm import Session

from app.agents import router
from app.agents.tools import TOOLS, ToolResult, invoke_tool
from app.container import Services
from app.models.orm import Conversation
from app.models.schemas import ChatResponse, Citation, ToolCallOut
from app.observability.telemetry import metrics_registry, traced
from app.repositories import ConversationRepository
from app.security.principal import Principal
from app.security.prompt_guard import is_privilege_escalation, looks_like_injection
from app.security.redaction import contains_secret, redact_text

HELP = ("I'm the IT Support Agent. I can:\n"
        "- answer IT how-to and policy questions from the knowledge base (e.g. \"How do I set up VPN?\")\n"
        "- create and track IT tickets (e.g. \"My laptop screen is flickering\", \"status of INC1001001\")\n"
        "- request application access with manager approval (e.g. \"I need access to Salesforce\")")


class AgentOrchestrator:
    def __init__(self, db: Session, services: Services):
        self.db = db
        self.s = services
        self.conversations = ConversationRepository(db)

    # ------------------------------------------------------------------ entry point
    def chat(self, principal: Principal, message: str, conversation_id: str | None) -> ChatResponse:
        start = time.perf_counter()
        conv = self._load(principal, conversation_id)
        state = dict(conv.state or {})
        self._calls: list[ToolCallOut] = []
        with traced("agent.turn", conversation_id=conv.id) as span:
            resp = self._handle(principal, conv, state, message)
            span.set_attribute("agent.intent", resp.intent)
        resp.tool_calls = self._calls
        conv.state = state
        transcript = list(conv.transcript or [])[-18:]
        transcript += [{"role": "user", "text": redact_text(message)}, {"role": "agent", "text": resp.reply[:2000]}]
        conv.transcript = transcript
        elapsed = (time.perf_counter() - start) * 1000
        failed = any(c.status != "success" for c in self._calls)
        metrics_registry.inc("agent_executions", intent=resp.intent, outcome="tool_error" if failed else "ok")
        metrics_registry.observe("agent_latency_ms", elapsed, intent=resp.intent)
        return resp

    def _load(self, principal: Principal, conversation_id: str | None) -> Conversation:
        if conversation_id:
            conv = self.conversations.get(conversation_id)
            if conv and conv.principal_id == principal.id:   # conversations are private to their owner
                return conv
        return self.conversations.add(Conversation(principal_id=principal.id, state={}, transcript=[]))

    def _tool(self, name: str, principal: Principal, args, ctx: dict | None = None) -> ToolResult:
        result = invoke_tool(name, self.s, principal, args, ctx)
        self._calls.append(ToolCallOut(tool=name, status="success" if result.ok else "failure",
                                       latency_ms=result.latency_ms))
        return result

    def _reply(self, conv: Conversation, intent: str, reply: str, **kw) -> ChatResponse:
        return ChatResponse(conversation_id=conv.id, reply=reply, intent=intent, **kw)

    # ------------------------------------------------------------------ routing
    def _handle(self, principal: Principal, conv: Conversation, state: dict, message: str) -> ChatResponse:
        # 1. guardrails (before anything else)
        if contains_secret(message):
            state.clear()
            return self._reply(conv, "secret_detected",
                               "It looks like your message contains a password or other secret. I haven't stored it. "
                               "Please never share credentials in chat - change that password now via "
                               "https://aka.ms/sspr (self-service password reset).")
        if is_privilege_escalation(message) or looks_like_injection(message):
            state.clear()
            return self._reply(conv, "refused",
                               "I can't help with that. I can't bypass approval or security policies, and "
                               "privileged directory roles are not requestable through this agent. If you need "
                               "elevated access, submit a normal access request for a catalog role and your manager "
                               "will review it.")

        # 2. continue an in-progress dialog
        awaiting = state.get("awaiting")
        if awaiting:
            if router.is_no(message):
                state.clear()
                return self._reply(conv, "cancelled", "No problem - I've cancelled that. Anything else?")
            intent = router.route(message)
            if intent.name in {"ticket_status", "access_status", "list_tickets", "help", "escalate",
                               "approval_attempt", "on_behalf_refused"}:
                state.clear()   # user changed topic
            elif awaiting == "ticket_confirm":
                return self._confirm_ticket(principal, conv, state, message)
            elif awaiting == "access_confirm":
                return self._confirm_access(principal, conv, state, message)
            elif awaiting == "ticket_description":
                return self._draft_ticket(conv, state, message)
            elif awaiting.startswith("access_"):
                return self._fill_access(principal, conv, state, message)

        intent = router.route(message)
        handler = {
            "help": lambda: self._reply(conv, "help", HELP),
            "escalate": lambda: self._escalate(conv),
            "approval_attempt": lambda: self._reply(
                conv, "approval_refused",
                "I can't approve or reject access requests. Decisions are made only by the assigned approver "
                "in the Approvals app in Teams (or the Approvals page in this demo)."),
            "on_behalf_refused": lambda: self._reply(
                conv, "on_behalf_refused",
                "I can only submit access requests for you, the signed-in user. Please ask your colleague to "
                "request access themselves so their manager can approve it."),
            "ticket_status": lambda: self._ticket_status(principal, conv, intent.ticket_number),
            "list_tickets": lambda: self._list_tickets(principal, conv),
            "access_status": lambda: self._access_status(principal, conv, intent.request_number),
            "access_request": lambda: self._start_access(principal, conv, state, message),
            "ticket_create": lambda: self._draft_ticket(conv, state, message),
            "knowledge": lambda: self._knowledge(principal, conv, message),
        }[intent.name]
        return handler()

    # ------------------------------------------------------------------ knowledge
    def _knowledge(self, principal: Principal, conv: Conversation, question: str) -> ChatResponse:
        result = self._tool("search_it_knowledge", principal, {"question": question[:1000]})
        if not result.ok:
            return self._reply(conv, "knowledge", "The knowledge service is unavailable right now. "
                                                  "Would you like me to create a ticket instead?")
        ans = result.data
        return self._reply(conv, "knowledge", ans.answer, citations=[Citation(**c.model_dump()) for c in ans.citations],
                           data={"grounded": ans.grounded, "fallback": ans.fallback,
                                 "blocked_content_count": ans.blocked_content_count},
                           suggested_replies=["Create a ticket"] if ans.fallback else [])

    def _escalate(self, conv: Conversation) -> ChatResponse:
        return self._reply(conv, "escalate", "I'll get you to a person. Contact the IT Service Desk in the "
                                             "'IT Help' Teams channel or call ext. 4357 (HELP). If you'd like, I can "
                                             "also create a ticket so they have the details - just describe the issue.")

    # ------------------------------------------------------------------ tickets
    def _draft_ticket(self, conv: Conversation, state: dict, message: str) -> ChatResponse:
        stripped = message.strip()
        generic = len(stripped) < 25 and router._TICKET_CREATE.search(stripped) and \
            not any(w in stripped.lower() for w in ("laptop", "vpn", "email", "printer", "password"))
        if generic or (len(stripped) < 10):
            state.clear()
            state["awaiting"] = "ticket_description"
            return self._reply(conv, "ticket_create", "Sure - please describe the problem: what's happening, "
                                                      "any error message, and what you've already tried.")
        fields = router.infer_ticket_fields(stripped)
        draft = {**fields, "description": redact_text(stripped)[:4000]}
        if len(draft["description"]) < 10:
            draft["description"] = (draft["description"] + " (details to follow)")[:4000]
        state.clear()
        state.update({"awaiting": "ticket_confirm", "ticket_draft": draft})
        note = "\n\nP1 means many users or a business-critical system are affected - say \"no\" if that's not the case." \
            if draft["priority"] == "P1" else ""
        return self._reply(conv, "ticket_create",
                           f"I'll create this ticket:\n- Title: {draft['title']}\n- Category: {draft['category']}\n"
                           f"- Priority: {draft['priority']}\n\nShall I submit it? (yes/no){note}",
                           data={"draft": draft}, suggested_replies=["Yes", "No"])

    def _confirm_ticket(self, principal: Principal, conv: Conversation, state: dict, message: str) -> ChatResponse:
        if not router.is_yes(message):
            return self._reply(conv, "ticket_create", "Please reply yes to create the ticket or no to cancel.",
                               suggested_replies=["Yes", "No"])
        draft = state.get("ticket_draft", {})
        key = "chat-" + hashlib.sha256(f"{conv.id}{json.dumps(draft, sort_keys=True)}".encode()).hexdigest()[:40]
        result = self._tool("create_it_ticket", principal, draft, {"idempotency_key": key})
        state.clear()
        if not result.ok:
            return self._reply(conv, "ticket_create", f"I couldn't create the ticket: {result.error_message}")
        t = result.data
        return self._reply(conv, "ticket_create",
                           f"Done - I created {t.ticket_number} ({t.priority}, {t.category}). "
                           f"You can ask me \"status of {t.ticket_number}\" any time.",
                           data={"ticket": t.model_dump(mode="json")})

    def _ticket_status(self, principal: Principal, conv: Conversation, number: str) -> ChatResponse:
        result = self._tool("get_ticket_status", principal, {"ticket_number": number})
        if not result.ok:
            return self._reply(conv, "ticket_status", f"I can't find ticket {number} among the tickets you can see.")
        t = result.data
        return self._reply(conv, "ticket_status", f"{t.ticket_number} is **{t.status.replace('_', ' ')}** "
                                                  f"(priority {t.priority}).", data={"ticket": t.model_dump(mode="json")})

    def _list_tickets(self, principal: Principal, conv: Conversation) -> ChatResponse:
        result = self._tool("list_my_tickets", principal, {})
        items = result.data or []
        if not items:
            return self._reply(conv, "list_tickets", "You don't have any tickets yet.")
        lines = "\n".join(f"- {t.ticket_number}: {t.status.replace('_', ' ')} ({t.priority})" for t in items)
        return self._reply(conv, "list_tickets", f"Your recent tickets:\n{lines}")

    # ------------------------------------------------------------------ access requests
    def _catalog(self, principal: Principal) -> list[dict]:
        result = self._tool("list_requestable_applications", principal, {})
        return result.data or []

    def _start_access(self, principal: Principal, conv: Conversation, state: dict, message: str) -> ChatResponse:
        state.clear()
        state["access_draft"] = {}
        state["awaiting"] = "access_application"
        return self._fill_access(principal, conv, state, message, initial=True)

    def _fill_access(self, principal: Principal, conv: Conversation, state: dict, message: str,
                     initial: bool = False) -> ChatResponse:
        catalog = self._catalog(principal)
        by_id = {a["id"]: a for a in catalog}
        draft = state.setdefault("access_draft", {})
        awaiting = state.get("awaiting")

        if not draft.get("application_id"):
            app_id = router.extract_application(message, catalog)
            if app_id:
                draft["application_id"] = app_id
        app = by_id.get(draft.get("application_id", ""))
        if app and not draft.get("role"):
            role = router.extract_role(message, app["roles"]) if (awaiting == "access_role" or initial) else None
            if role:
                draft["role"] = role
        if not draft.get("duration_days"):
            d = router.extract_duration(message)
            if d:
                draft["duration_days"] = d
        if not draft.get("justification"):
            j = router.extract_justification(message) if initial or awaiting != "access_justification" else None
            if awaiting == "access_justification":
                j = message.strip() if len(message.strip()) >= 20 else None
            if j:
                draft["justification"] = redact_text(j)[:1000]

        # ask for the next missing slot
        if not app:
            state["awaiting"] = "access_application"
            names = ", ".join(a["name"] for a in catalog)
            prefix = "" if initial else "I couldn't match that to a requestable application. "
            return self._reply(conv, "access_request", f"{prefix}Which application do you need access to? "
                                                       f"Requestable applications: {names}.",
                               suggested_replies=[a["name"] for a in catalog[:4]])
        if not draft.get("role"):
            state["awaiting"] = "access_role"
            roles = "\n".join(f"- {r['name']}: {r['description']}" + (" (privileged - extra approval)" if r["privileged"] else "")
                              for r in app["roles"])
            least = min(app["roles"], key=lambda r: r["level"])["name"]
            return self._reply(conv, "access_request", f"Which role do you need on {app['name']}? Please choose the "
                                                       f"least privilege that lets you do your work:\n{roles}",
                               suggested_replies=[least] + [r["name"] for r in app["roles"] if r["name"] != least][:2])
        role = next(r for r in app["roles"] if r["name"] == draft["role"])
        max_days = min(app["max_duration_days"], role.get("max_duration_days", app["max_duration_days"]))
        if not draft.get("duration_days") or draft["duration_days"] > max_days:
            exceeded = draft.pop("duration_days", None)
            state["awaiting"] = "access_duration"
            pre = f"{exceeded} days exceeds the policy maximum. " if exceeded else ""
            return self._reply(conv, "access_request", f"{pre}For how long do you need it? (max {max_days} days for "
                                                       f"{role['name']})", suggested_replies=["30 days", "7 days"])
        if not draft.get("justification"):
            state["awaiting"] = "access_justification"
            return self._reply(conv, "access_request", "What's the business justification? (at least 20 characters - "
                                                       "your approver will see this)")
        state["awaiting"] = "access_confirm"
        priv = "\nThis is a privileged role: it needs your manager's AND the application owner's approval." \
            if role["privileged"] else ""
        return self._reply(conv, "access_request",
                           f"Please confirm your access request:\n- Application: {app['name']}\n- Role: {role['name']}\n"
                           f"- Duration: {draft['duration_days']} days\n- Justification: {draft['justification']}"
                           f"{priv}\n\nSubmit for approval? (yes/no)", data={"draft": draft},
                           suggested_replies=["Yes", "No"])

    def _confirm_access(self, principal: Principal, conv: Conversation, state: dict, message: str) -> ChatResponse:
        if not router.is_yes(message):
            return self._reply(conv, "access_request", "Please reply yes to submit or no to cancel.",
                               suggested_replies=["Yes", "No"])
        draft = state.get("access_draft", {})
        key = "chat-" + hashlib.sha256(f"{conv.id}{json.dumps(draft, sort_keys=True)}".encode()).hexdigest()[:40]
        result = self._tool("submit_access_request", principal, draft, {"idempotency_key": key})
        state.clear()
        if not result.ok:
            friendly = {
                "DUPLICATE_REQUEST": "You already have an active request or access for that role.",
                "APPROVAL_SERVICE_UNAVAILABLE": "The approval service is unavailable, so nothing was submitted. "
                                                "Please try again in a few minutes.",
                "IDENTITY_PROVIDER_UNAVAILABLE": "I couldn't look up your manager right now. Please try again later.",
                "NO_ELIGIBLE_APPROVER": "No eligible approver was found for this request - IT access governance "
                                        "needs to review your directory record.",
            }.get(result.error_code or "", result.error_message)
            return self._reply(conv, "access_request", f"I couldn't submit the request: {friendly}",
                               data={"error_code": result.error_code})
        r = result.data
        approver = r.approvals[0].approver_upn if r.approvals else "your approver"
        return self._reply(conv, "access_request",
                           f"Submitted {r.request_number}. It's waiting for approval from {approver}. I'll notify you "
                           f"when there's a decision - I can't approve it myself.",
                           data={"access_request": r.model_dump(mode="json")})

    def _access_status(self, principal: Principal, conv: Conversation, number: str | None) -> ChatResponse:
        result = self._tool("get_access_request_status", principal, {"request_number": number})
        if not result.ok:
            return self._reply(conv, "access_status", f"I can't find access request {number}.")
        items = result.data
        if not items:
            return self._reply(conv, "access_status", "You don't have any access requests yet.")
        lines = []
        for r in items:
            pending = next((a for a in r.approvals if a.status == "pending"), None)
            extra = f" - waiting on {pending.approver_upn} (stage {pending.stage}/{r.required_stages})" if pending else ""
            lines.append(f"- {r.request_number}: {r.application_id}/{r.role} is **{r.status.replace('_', ' ')}**{extra}")
        return self._reply(conv, "access_status", "\n".join(lines),
                           data={"access_requests": [r.model_dump(mode="json") for r in items]})


__all__ = ["AgentOrchestrator", "TOOLS"]
