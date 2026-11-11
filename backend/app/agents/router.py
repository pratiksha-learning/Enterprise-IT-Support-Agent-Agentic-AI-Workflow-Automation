"""Deterministic intent router + slot extraction.

This is the *local emulation* of Copilot Studio generative orchestration so the demo runs
offline. In the tenant deployment, Copilot Studio's LLM planner selects tools using the
descriptions in ``copilot-studio/tool-definitions``; the same backend tools are called either way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TICKET_RE = re.compile(r"\bINC\d{7}\b", re.IGNORECASE)
ACCESS_RE = re.compile(r"\bACC\d{6}\b", re.IGNORECASE)

_YES = re.compile(r"^\s*(yes|y|yep|yeah|confirm|confirmed|submit|go ahead|ok|okay|sure|please do)\b", re.I)
_NO = re.compile(r"^\s*(no|n|nope|cancel|stop|never ?mind|abort|don'?t)\b", re.I)

_ACCESS_PATTERNS = re.compile(
    r"(need|want|request|requesting|get|give me|grant me|require|like)\b.{0,40}\baccess\b|\baccess (to|for)\b|"
    r"\brequest (a )?(role|permission)|\bpermission(s)? (to|for|on)\b|\badd me to\b", re.I)
_ACCESS_STATUS = re.compile(r"\b(my|status of( my)?) access requests?\b|\baccess requests? status\b", re.I)
_MY_TICKETS = re.compile(r"\b(my|list( my)?|show( my)?) (open )?tickets\b", re.I)
_TICKET_CREATE = re.compile(
    r"\b(create|open|raise|log|file|submit)\b.{0,15}\b(ticket|incident|case)\b|\b(not working|isn'?t working|"
    r"doesn'?t work|broken|crash(es|ed|ing)?|error|fails?|failing|won'?t (start|turn on|boot|connect|open)|"
    r"can'?t (log ?in|connect|access|print|open|sign ?in)|cannot (log ?in|connect|print|open|sign ?in)|"
    r"flicker(s|ing)?|frozen|freez(es|ing)|slow|outage|down\b|stopped working)", re.I)
_APPROVE_ATTEMPT = re.compile(r"\b(approve|reject|deny)\b.{0,40}\b(requests?|ACC\d{6}|access)\b|"
                              r"\bmark\b.{0,20}\bapproved\b", re.I)
_ON_BEHALF = re.compile(
    r"\bon behalf of\b|\bfor (my )?(colleague|coworker|co-worker|teammate|team ?mate|boss|manager|friend|team)\b|"
    r"\b(give|grant|add)\s+(?!me\b|us\b|my\b|myself\b)([A-Z][a-z]+|him|her|them)\b.{0,30}\baccess\b", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|help|what can you do)\b[\s!?.]*$", re.I)
_HUMAN = re.compile(r"\b(talk to|speak (to|with)|connect me (to|with)|want) (a )?(human|person|agent|someone)\b", re.I)


@dataclass
class RoutedIntent:
    name: str
    ticket_number: str | None = None
    request_number: str | None = None


def is_yes(text: str) -> bool:
    return bool(_YES.match(text))


def is_no(text: str) -> bool:
    return bool(_NO.match(text))


def route(message: str) -> RoutedIntent:
    if _GREETING.match(message):
        return RoutedIntent("help")
    if _HUMAN.search(message):
        return RoutedIntent("escalate")
    if _APPROVE_ATTEMPT.search(message):
        return RoutedIntent("approval_attempt")
    if m := TICKET_RE.search(message):
        return RoutedIntent("ticket_status", ticket_number=m.group(0).upper())
    if m := ACCESS_RE.search(message):
        return RoutedIntent("access_status", request_number=m.group(0).upper())
    if _ACCESS_STATUS.search(message):
        return RoutedIntent("access_status")
    if _MY_TICKETS.search(message):
        return RoutedIntent("list_tickets")
    if _ACCESS_PATTERNS.search(message):
        if _ON_BEHALF.search(message):
            return RoutedIntent("on_behalf_refused")
        return RoutedIntent("access_request")
    if _TICKET_CREATE.search(message) and not re.match(r"^\s*(how|what|where|why|when|which|can i|do i)\b", message, re.I):
        return RoutedIntent("ticket_create")
    return RoutedIntent("knowledge")


# ------------------------------------------------------------------ slot extraction
_DURATION = re.compile(r"\b(\d{1,3})\s*(day|days|d|week|weeks|wk|month|months|mo)\b", re.I)
_WORD_DURATION = {"a week": 7, "one week": 7, "two weeks": 14, "a month": 30, "one month": 30,
                  "a day": 1, "one day": 1, "three months": 90, "six months": 180, "a year": 365}


def extract_duration(text: str) -> int | None:
    if m := _DURATION.search(text):
        n, unit = int(m.group(1)), m.group(2).lower()
        return n * 7 if unit.startswith("w") else n * 30 if unit.startswith("mo") or unit == "months" else n
    lower = text.lower()
    for phrase, days in _WORD_DURATION.items():
        if phrase in lower:
            return days
    return None


def extract_application(text: str, catalog: list[dict]) -> str | None:
    lower = f" {text.lower()} "
    best, best_len = None, 0
    for app in catalog:
        for term in [app["id"], app["name"].lower(), *app.get("aliases", [])]:
            term_l = term.lower()
            if re.search(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", lower) and len(term_l) > best_len:
                best, best_len = app["id"], len(term_l)
    return best


_ROLE_HINTS = {
    1: ("read", "read-only", "readonly", "view", "viewer", "visitor", "reader", "basic", "user"),
    2: ("edit", "write", "contribute", "contributor", "member", "sales"),
    3: ("admin", "administrator", "owner", "maintainer", "manage", "power"),
}


def extract_role(text: str, roles: list[dict]) -> str | None:
    lower = text.lower()
    for r in roles:
        if re.search(rf"\b{re.escape(r['name'].replace('_', ' '))}\b", lower.replace("_", " ")):
            return r["name"]
    for level, words in _ROLE_HINTS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", lower) for w in words):
            candidates = [r for r in roles if r["level"] == level]
            if candidates:
                return candidates[0]["name"]
    return None


def extract_justification(text: str) -> str | None:
    m = re.search(r"\b(because|since|so that|as i need|i need it (to|for)|for the purpose of|justification:?)\s+(.+)",
                  text, re.I)
    candidate = m.group(0) if m else None
    if candidate and len(candidate.strip()) >= 20:
        return candidate.strip()[:1000]
    return None


def infer_ticket_fields(text: str) -> dict:
    lower = text.lower()
    category = "other"
    for cat, words in {
        "hardware": ("laptop", "screen", "monitor", "keyboard", "mouse", "printer", "battery", "dock", "headset", "flicker"),
        "network": ("vpn", "wifi", "wi-fi", "wireless", "network", "internet", "ethernet"),
        "account": ("password", "locked", "lockout", "mfa", "authenticator", "sign in", "log in", "login"),
        "software": ("outlook", "teams", "excel", "word", "install", "update", "application", "app", "software", "crash"),
        "access": ("permission", "access denied", "access"),
    }.items():
        if any(w in lower for w in words):
            category = cat
            break
    priority = "P3"
    if re.search(r"\b(everyone|whole (team|floor|office)|all users|company[- ]wide|outage|entire)\b", lower):
        priority = "P1"
    elif re.search(r"\b(can'?t work|cannot work|completely blocked|blocked|urgent|asap)\b", lower):
        priority = "P2"
    elif re.search(r"\b(question|request|whenever|no rush|nice to have)\b", lower):
        priority = "P4"
    first = re.split(r"(?<=[.!?])\s+", text.strip())[0]
    title = first if len(first) <= 80 else first[:77].rsplit(" ", 1)[0] + "..."
    if len(title) < 5:
        title = f"IT issue: {title}"
    return {"title": title, "category": category, "priority": priority}
