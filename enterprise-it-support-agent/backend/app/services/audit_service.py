"""Tamper-evident audit trail.

Each entry stores ``hash = sha256(prev_hash || canonical_json(entry))``. Modifying or
deleting any historic row breaks the chain, which ``verify()`` detects. Details are
redacted before they are persisted. (Production: also stream entries to Log Analytics /
Microsoft Sentinel and make the table append-only via DB permissions.)
"""
from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.models.orm import AuditLog, as_utc, utcnow
from app.observability.context import get_correlation_id
from app.repositories import AuditRepository
from app.security.principal import Principal
from app.security.redaction import redact_value

logger = logging.getLogger("audit")
GENESIS = "0" * 64


def _entry_hash(prev_hash: str, e: AuditLog) -> str:
    canonical = json.dumps({
        "ts": as_utc(e.timestamp).isoformat(), "cid": e.correlation_id, "actor": e.actor_id,
        "actor_type": e.actor_type, "action": e.action, "target_type": e.target_type, "target": e.target_id,
        "outcome": e.outcome, "details": e.details,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


class AuditService:
    def __init__(self, db: Session):
        self.repo = AuditRepository(db)

    def record(self, *, action: str, target_type: str, target_id: str, outcome: str = "success",
               actor: Principal | None = None, actor_id: str | None = None, actor_type: str | None = None,
               details: dict | None = None) -> AuditLog:
        prev = self.repo.last()
        entry = AuditLog(
            timestamp=utcnow().replace(microsecond=0),
            correlation_id=get_correlation_id(),
            actor_id=actor.id if actor else (actor_id or "system"),
            actor_type=actor_type or (actor.auth_type if actor else "system"),
            action=action, target_type=target_type, target_id=str(target_id), outcome=outcome,
            details=redact_value(details or {}, mask_emails=True),
            prev_hash=prev.hash if prev else GENESIS,
        )
        entry.hash = _entry_hash(entry.prev_hash, entry)
        self.repo.add(entry)
        logger.info("audit", extra={"action": action, "target": f"{target_type}:{target_id}", "outcome": outcome})
        return entry

    def verify(self) -> tuple[bool, int, int | None]:
        prev = GENESIS
        entries = self.repo.all_ordered()
        for e in entries:
            if e.prev_hash != prev or _entry_hash(prev, e) != e.hash:
                return False, len(entries), e.id
            prev = e.hash
        return True, len(entries), None
