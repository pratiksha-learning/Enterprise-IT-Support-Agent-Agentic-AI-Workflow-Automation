"""Sensitive-information redaction.

Used by the log formatter, the audit service and the agent orchestrator so that
passwords, tokens, secrets and personal identifiers never reach logs or the audit trail.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "client_secret", "token", "access_token",
    "refresh_token", "id_token", "authorization", "api_key", "apikey", "callback_token",
    "x-callback-token", "otp", "mfa_code", "ssn", "credit_card", "connection_string",
}

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWTs (three base64url segments)
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED_JWT]"),
    # Bearer tokens
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    # key=value / key: value secrets in free text
    (re.compile(r"(?i)\b(password|passwd|pwd|passcode|secret|api[_-]?key|token|otp|pin)\b\s*(?:is|=|:)\s*\S+"),
     r"\1: [REDACTED]"),
    # Signed URL query parameters (e.g. Power Automate sig=)
    (re.compile(r"(?i)([?&](?:sig|signature|code|key)=)[^&\s]+"), r"\1[REDACTED]"),
    # Payment card numbers (13-16 digits, optional separators)
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_CARD]"),
    # US SSN style / national id style
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_ID]"),
    # Phone numbers
    (re.compile(r"(?<!\w)\+?\d{1,3}[ -]?\(?\d{2,4}\)?[ -]?\d{3,4}[ -]?\d{3,4}(?!\w)"), "[REDACTED_PHONE]"),
]

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def redact_text(text: str, mask_emails: bool = False) -> str:
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    if mask_emails:
        out = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", out)
    return out


def contains_secret(text: str) -> bool:
    """True if the text appears to contain a credential the user should not have shared."""
    return redact_text(text) != text and bool(
        re.search(r"(?i)\b(password|passwd|pwd|passcode|secret|api[_-]?key|token|otp|pin)\b|eyJ|bearer", text)
    )


def redact_value(value: Any, mask_emails: bool = False, _depth: int = 0) -> Any:
    """Recursively redact dicts/lists/strings."""
    if _depth > 8:
        return REDACTED
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                result[k] = REDACTED
            else:
                result[k] = redact_value(v, mask_emails, _depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(v, mask_emails, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value, mask_emails)
    return value
