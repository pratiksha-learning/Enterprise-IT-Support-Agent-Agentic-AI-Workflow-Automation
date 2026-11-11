"""Composition root: builds adapters from configuration and per-request service objects."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import AuthMode, Settings
from app.integrations.approvals import MockApprovalProvider, PowerAutomateApprovalProvider
from app.integrations.common import FaultInjector
from app.integrations.graph_client import GraphClient
from app.integrations.identity import GraphIdentityProvider, MockIdentityProvider
from app.integrations.llm import (AzureOpenAIAnswerGenerator, AzureOpenAIEmbedder, ExtractiveAnswerGenerator,
                                  LocalHashingEmbedder)
from app.integrations.notifications import MockNotificationProvider, TeamsFlowNotificationProvider
from app.integrations.provisioning import EntraGroupProvisioningProvider, MockProvisioningProvider
from app.models.orm import Application
from app.repositories import ApplicationRepository
from app.security.auth import EntraTokenValidator, MockDirectory, MockTokenService
from app.security.rate_limit import TokenBucketLimiter
from app.security.secrets import build_secret_provider
from app.services.access_service import AccessRequestService
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.knowledge import KnowledgeService
from app.services.knowledge.ingestion import LocalDirectorySource
from app.services.notification_service import NotificationService
from app.services.ticket_service import TicketService

logger = logging.getLogger(__name__)


def _secret(settings: Settings, provider, attr: str) -> str | None:
    value = getattr(settings, attr)
    if value is not None:
        return value.get_secret_value()
    return provider.get(attr.upper())


class Container:
    """Process-wide singletons (adapters are stateless or thread-safe)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.faults = FaultInjector()
        self.directory = MockDirectory(settings.mock_directory_path)
        secrets = build_secret_provider(settings.key_vault_url)
        self.catalog_data = json.loads(settings.application_catalog_path.read_text(encoding="utf-8"))["applications"]

        graph = None
        if "graph" in (settings.identity_provider,) or settings.provisioning_provider == "entra_groups" \
                or settings.knowledge_source == "sharepoint":
            secret = _secret(settings, secrets, "graph_client_secret")
            if not (settings.graph_tenant_id and settings.graph_client_id and secret):
                raise ValueError("Graph-based adapters require GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET")
            graph = GraphClient(settings.graph_tenant_id, settings.graph_client_id, secret)
        self.graph = graph

        # --- authentication
        if settings.auth_mode == AuthMode.MOCK:
            self.mock_tokens: MockTokenService | None = MockTokenService(
                settings.mock_jwt_secret.get_secret_value(), self.directory, settings.mock_token_ttl_minutes)
            self.entra_validator = None
        else:
            self.mock_tokens = None
            self.entra_validator = EntraTokenValidator(settings.entra_tenant_id, settings.entra_api_audience,
                                                       settings.callback_app_ids)

        # --- adapters
        self.identity = (GraphIdentityProvider(graph) if settings.identity_provider == "graph"
                         else MockIdentityProvider(self.directory, self.faults))
        if settings.approval_provider == "power_automate":
            url = _secret(settings, secrets, "power_automate_approval_flow_url")
            if not url:
                raise ValueError("APPROVAL_PROVIDER=power_automate requires POWER_AUTOMATE_APPROVAL_FLOW_URL")
            self.approvals = PowerAutomateApprovalProvider(url)
        else:
            self.approvals = MockApprovalProvider(self.faults)
        if settings.provisioning_provider == "entra_groups":
            allowed = {r["group_id"] for a in self.catalog_data for r in a["roles"]}
            self.provisioning = EntraGroupProvisioningProvider(graph, allowed)
        else:
            self.provisioning = MockProvisioningProvider(self.faults)
        if settings.notification_provider == "teams_webhook":
            url = _secret(settings, secrets, "power_automate_notify_flow_url")
            if not url:
                raise ValueError("NOTIFICATION_PROVIDER=teams_webhook requires POWER_AUTOMATE_NOTIFY_FLOW_URL")
            self.notifier = TeamsFlowNotificationProvider(url)
        else:
            self.notifier = MockNotificationProvider(self.faults)

        aoai_key = _secret(settings, secrets, "azure_openai_api_key")
        if settings.embedding_provider == "azure_openai":
            self.embedder = AzureOpenAIEmbedder(settings.azure_openai_endpoint, aoai_key,
                                                settings.azure_openai_embedding_deployment,
                                                settings.azure_openai_api_version)
        else:
            self.embedder = LocalHashingEmbedder()
        if settings.answer_provider == "azure_openai":
            self.generator = AzureOpenAIAnswerGenerator(settings.azure_openai_endpoint, aoai_key,
                                                        settings.azure_openai_chat_deployment,
                                                        settings.azure_openai_api_version)
        else:
            self.generator = ExtractiveAnswerGenerator()

        self.rate_limiter = TokenBucketLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)

    def knowledge_source(self):
        if self.settings.knowledge_source == "sharepoint":
            from app.integrations.sharepoint import SharePointGraphSource

            return SharePointGraphSource(self.graph, self.settings.sharepoint_site_id, self.settings.sharepoint_drive_id)
        return LocalDirectorySource(self.settings.knowledge_base_path)

    def seed_catalog(self, db: Session) -> None:
        repo = ApplicationRepository(db)
        for raw in self.catalog_data:
            repo.upsert(Application(id=raw["id"], name=raw["name"], description=raw.get("description", ""),
                                    owner_id=raw["owner_id"], aliases=raw.get("aliases", []), roles=raw["roles"],
                                    max_duration_days=raw["max_duration_days"]))
        db.commit()


@dataclass
class Services:
    audit: AuditService
    tickets: TicketService
    notifications: NotificationService
    access: AccessRequestService
    knowledge: KnowledgeService
    idempotency: IdempotencyService
    db: Session

    def commit(self) -> None:
        """Commit before the response is built so a failed commit can never yield a 2xx."""
        self.db.commit()


def build_services(db: Session, c: Container) -> Services:
    s = c.settings
    audit = AuditService(db)
    tickets = TicketService(db, audit)
    notifications = NotificationService(db, c.notifier, audit, attempts=s.downstream_max_attempts,
                                        backoff=s.downstream_backoff_seconds)
    access = AccessRequestService(db, s, c.identity, c.approvals, c.provisioning, notifications, audit, tickets)
    knowledge = KnowledgeService(db, c.embedder, c.generator, top_k=s.rag_top_k, min_score=s.rag_min_score,
                                 chunk_size=s.chunk_size, chunk_overlap=s.chunk_overlap)
    return Services(audit, tickets, notifications, access, knowledge, IdempotencyService(db, s.idempotency_ttl_hours), db)
