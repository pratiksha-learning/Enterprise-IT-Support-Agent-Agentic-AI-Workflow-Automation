"""Tool registry exposed to the agent.

These are the ONLY actions the agent can take. Each tool:
* declares the permission it needs (checked against the *signed-in user*, not the LLM),
* validates its input with the same Pydantic schema as the REST API,
* is traced and counted (success/failure/latency).

There is deliberately no ``approve_access_request`` tool: approval decisions can only be
made by the assigned human approver through the approval channel.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.container import Services
from app.errors import AppError, AuthorizationError
from app.models.schemas import (AccessRequestCreate, AccessRequestOut, KnowledgeQuery, TicketCreate, TicketOut,
                                TicketStatusOut)
from app.observability.telemetry import metrics_registry, traced
from app.security.principal import Principal
from app.security.rbac import Permission, has_permission


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float = 0.0


@dataclass
class Tool:
    name: str
    description: str
    permission: Permission
    input_model: type[BaseModel] | None
    handler: Callable[[Services, Principal, Any, dict], Any]
    side_effect: bool = False
    metadata: dict = field(default_factory=dict)


def _search_knowledge(s: Services, p: Principal, args: KnowledgeQuery, ctx: dict):
    return s.knowledge.answer(p, args.question)


def _create_ticket(s: Services, p: Principal, args: TicketCreate, ctx: dict):
    key = ctx.get("idempotency_key")
    if key:
        h = hashlib.sha256(json.dumps(args.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        prior = s.idempotency.lookup(p.id, "agent.create_ticket", key, h)
        if prior:
            return TicketOut.model_validate(prior.response_body)
    ticket = s.tickets.create(p, args, source="agent")
    out = TicketOut.model_validate(ticket)
    if key:
        s.idempotency.store(p.id, "agent.create_ticket", key, h, 201, out.model_dump(mode="json"))
    return out


def _get_ticket_status(s: Services, p: Principal, args: dict, ctx: dict):
    return TicketStatusOut.model_validate(s.tickets.get(p, args["ticket_number"]))


def _list_my_tickets(s: Services, p: Principal, args: dict, ctx: dict):
    items, _ = s.tickets.list(p, status=None, mine_only=True, limit=5, offset=0)
    return [TicketStatusOut.model_validate(t) for t in items]


def _submit_access_request(s: Services, p: Principal, args: AccessRequestCreate, ctx: dict):
    key = ctx["idempotency_key"]
    h = hashlib.sha256(json.dumps(args.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    prior = s.idempotency.lookup(p.id, "access_requests.create", key, h)
    if prior:
        return AccessRequestOut.model_validate(prior.response_body)
    req = s.access.submit(p, args)
    out = AccessRequestOut.model_validate(req)
    s.idempotency.store(p.id, "access_requests.create", key, h, 201, out.model_dump(mode="json"))
    return out


def _get_access_request(s: Services, p: Principal, args: dict, ctx: dict):
    if args.get("request_number"):
        return [AccessRequestOut.model_validate(s.access.get(p, args["request_number"]))]
    items, _ = s.access.list(p, status=None, mine_only=True, limit=5, offset=0)
    return [AccessRequestOut.model_validate(r) for r in items]


def _list_applications(s: Services, p: Principal, args: dict, ctx: dict):
    return [{"id": a.id, "name": a.name, "aliases": a.aliases, "roles": a.roles,
             "max_duration_days": a.max_duration_days} for a in s.access.catalog()]


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("search_it_knowledge", "Answer IT how-to and policy questions from the IT knowledge base with citations.",
         Permission.KNOWLEDGE_QUERY, KnowledgeQuery, _search_knowledge),
    Tool("create_it_ticket", "Create an IT support ticket for the signed-in user.",
         Permission.TICKET_CREATE, TicketCreate, _create_ticket, side_effect=True),
    Tool("get_ticket_status", "Get the status of one of the user's tickets by number (INCxxxxxxx).",
         Permission.TICKET_READ_OWN, None, _get_ticket_status),
    Tool("list_my_tickets", "List the signed-in user's most recent tickets.",
         Permission.TICKET_READ_OWN, None, _list_my_tickets),
    Tool("list_requestable_applications", "List applications and roles that can be requested.",
         Permission.ACCESS_REQUEST, None, _list_applications),
    Tool("submit_access_request", "Submit an access request for the signed-in user; starts manager approval.",
         Permission.ACCESS_REQUEST, AccessRequestCreate, _submit_access_request, side_effect=True),
    Tool("get_access_request_status", "Get status of the user's access requests (optionally by ACCxxxxxx).",
         Permission.ACCESS_READ_OWN, None, _get_access_request),
]}


def invoke_tool(name: str, services: Services, principal: Principal, args: Any, ctx: dict | None = None) -> ToolResult:
    tool = TOOLS[name]
    start = time.perf_counter()
    outcome = "success"
    try:
        with traced("agent.tool", **{"tool.name": name, "tool.side_effect": tool.side_effect}):
            if not has_permission(principal, tool.permission):
                raise AuthorizationError(f"Not permitted to use {name}", code="TOOL_FORBIDDEN")
            if tool.input_model is not None and not isinstance(args, tool.input_model):
                args = tool.input_model.model_validate(args)
            data = tool.handler(services, principal, args, ctx or {})
        return ToolResult(ok=True, data=data, latency_ms=round((time.perf_counter() - start) * 1000, 2))
    except AppError as exc:
        outcome = "failure"
        return ToolResult(ok=False, error_code=exc.code, error_message=exc.message,
                          latency_ms=round((time.perf_counter() - start) * 1000, 2))
    except Exception as exc:  # validation errors etc.
        outcome = "failure"
        return ToolResult(ok=False, error_code=type(exc).__name__, error_message="The tool input was invalid.",
                          latency_ms=round((time.perf_counter() - start) * 1000, 2))
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        metrics_registry.inc("tool_invocations", tool=name, outcome=outcome)
        metrics_registry.observe("tool_latency_ms", elapsed, tool=name)
