"""Idempotency-Key handling (scoped per principal + operation).

* same key + same payload  -> stored response is replayed (no duplicate side effects)
* same key + other payload -> 422 IDEMPOTENCY_KEY_REUSED
* responses with 5xx are not stored so the client can safely retry
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.errors import ValidationFailed
from app.models.orm import IdempotencyRecord, as_utc, utcnow
from app.repositories import IdempotencyRepository

_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def validate_key(key: str | None, required: bool) -> str | None:
    if key is None:
        if required:
            raise ValidationFailed("Idempotency-Key header is required for this operation",
                                   code="IDEMPOTENCY_KEY_REQUIRED")
        return None
    if not _KEY_RE.match(key):
        raise ValidationFailed("Idempotency-Key must be 8-128 characters [A-Za-z0-9_.:-]",
                               code="IDEMPOTENCY_KEY_INVALID")
    return key


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class IdempotencyService:
    def __init__(self, db: Session, ttl_hours: int = 24):
        self.repo = IdempotencyRepository(db)
        self.ttl = timedelta(hours=ttl_hours)

    def lookup(self, principal_id: str, scope: str, key: str, req_hash: str) -> IdempotencyRecord | None:
        record = self.repo.get(principal_id, scope, key)
        if record is None:
            return None
        if as_utc(record.expires_at) < utcnow():
            self.repo.delete(record)
            return None
        if record.request_hash != req_hash:
            raise ValidationFailed("Idempotency-Key was already used with a different request payload",
                                   code="IDEMPOTENCY_KEY_REUSED")
        return record

    def store(self, principal_id: str, scope: str, key: str, req_hash: str, status: int, body: dict) -> None:
        if status >= 500:
            return
        self.repo.add(IdempotencyRecord(principal_id=principal_id, scope=scope, key=key, request_hash=req_hash,
                                        response_status=status, response_body=body,
                                        expires_at=utcnow() + self.ttl))
