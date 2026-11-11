"""Employee/approver notifications. Failures never break the business workflow."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.integrations.common import IntegrationError, with_retry
from app.integrations.notifications import NotificationProvider
from app.models.orm import Notification
from app.repositories import NotificationRepository
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Session, provider: NotificationProvider, audit: AuditService, attempts: int = 3,
                 backoff: float = 0.05):
        self.repo = NotificationRepository(db)
        self.provider = provider
        self.audit = audit
        self.attempts = attempts
        self.backoff = backoff

    def notify(self, recipient_id: str, recipient_upn: str, subject: str, body: str,
               related_id: str | None = None) -> Notification:
        status = "sent"
        try:
            with_retry("notification", lambda: self.provider.send(recipient_upn, subject, body),
                       attempts=self.attempts, backoff=self.backoff)
        except IntegrationError as exc:
            status = "failed"
            logger.error("notification delivery failed", extra={"recipient_id": recipient_id, "error": str(exc)})
        n = self.repo.add(Notification(recipient_id=recipient_id, channel=self.provider.name, subject=subject,
                                       body=body, status=status, related_id=related_id))
        self.audit.record(action="notification.sent" if status == "sent" else "notification.failed",
                          target_type="notification", target_id=related_id or recipient_id,
                          outcome="success" if status == "sent" else "failure",
                          details={"recipient_id": recipient_id, "subject": subject, "channel": self.provider.name})
        return n
