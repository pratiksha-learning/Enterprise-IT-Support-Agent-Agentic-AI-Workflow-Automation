"""Prompt-injection defences.

Two surfaces are protected:

1. **Indirect injection** - instructions hidden in retrieved documents. Retrieved text is
   treated as *data*: sentences that look like instructions to the AI are removed before
   the text is used, counted, and the remaining text is wrapped in explicit delimiters
   when sent to an LLM.
2. **Direct injection / privilege escalation** in user messages - detected so the agent
   refuses instead of calling tools.

This is defence-in-depth, not a guarantee: the hard controls are server-side
(authorisation, approval workflow, no approve tool exposed to the agent).
"""
from __future__ import annotations

import re

_INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any |the )?(previous|prior|above|your) (instructions|rules)",
    r"forget (all |your )?(previous )?(instructions|rules)",
    r"you are now\b", r"act as (an? )?(admin|administrator|system)", r"new instructions?:",
    r"\bsystem prompt\b", r"\bdeveloper mode\b", r"\bjailbreak\b",
    r"(approve|grant) (all|every|any) (access )?requests?", r"auto[- ]?approve",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>", r"\[\s*(system|inst)\s*\]",
    r"(reveal|print|show) (your|the) (instructions|prompt|secrets?|credentials)",
    r"\b(note|message|instruction)s? (to|for) (the )?(ai|assistant|agent|llm|copilot)\b",
    r"\bai (agents?|assistants?)\s*(must|should|shall)\b",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_ESCALATION_RE = re.compile(
    r"(domain admins?|global admin(istrator)?|enterprise admins?|root access|sudo|"
    r"bypass (the )?(approval|manager|policy)|without (manager |an? )?approval|skip (the )?approval|"
    r"approve (it|this|my request) (yourself|for me)|mark (it|this) (as )?approved)",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def is_privilege_escalation(text: str) -> bool:
    return bool(_ESCALATION_RE.search(text or ""))


def sanitize_retrieved_text(text: str) -> tuple[str, int]:
    """Remove instruction-like sentences from retrieved content. Returns (clean_text, removed_count)."""
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p is not None]
    kept, removed = [], 0
    for part in parts:
        if looks_like_injection(part):
            removed += 1
            continue
        kept.append(part)
    return " ".join(p.strip() for p in kept if p.strip()), removed


def wrap_untrusted(source_label: str, text: str) -> str:
    """Delimit retrieved content for an LLM prompt; strips any delimiter look-alikes first."""
    safe = text.replace("<<<", "").replace(">>>", "")
    return f"<<<UNTRUSTED_DOCUMENT source=\"{source_label}\">>>\n{safe}\n<<<END_UNTRUSTED_DOCUMENT>>>"
