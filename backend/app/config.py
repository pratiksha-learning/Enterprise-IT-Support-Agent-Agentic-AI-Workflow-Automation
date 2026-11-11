"""Application configuration.

All configuration comes from environment variables (optionally a local ``.env`` file).
Secrets are typed as ``SecretStr`` so they are never printed in logs or reprs.
Nothing in this module contains a real credential.
"""
from __future__ import annotations

import secrets
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class AuthMode(str, Enum):
    MOCK = "mock"      # locally signed HS256 tokens for demo personas
    ENTRA = "entra"    # Microsoft Entra ID access tokens (RS256, JWKS validated)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- general -----------------------------------------------------------
    app_name: str = "Enterprise IT Support Agent API"
    environment: str = Field(default="local", description="local | dev | test | prod")
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'backend' / 'data' / 'itsupport.db').as_posix()}"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_base_url: str = "http://localhost:8000"

    # --- authentication ------------------------------------------------------
    auth_mode: AuthMode = AuthMode.MOCK
    mock_jwt_secret: SecretStr | None = None
    mock_token_ttl_minutes: int = 120
    entra_tenant_id: str | None = None
    entra_api_audience: str | None = None          # e.g. api://<backend-app-client-id>
    entra_allowed_callback_app_ids: str = ""       # comma separated client IDs allowed to call approval callback

    # --- integrations (adapter selection) -----------------------------------
    identity_provider: str = "mock"          # mock | graph
    approval_provider: str = "mock"          # mock | power_automate
    provisioning_provider: str = "mock"      # mock | entra_groups
    notification_provider: str = "mock"      # mock | teams_webhook
    knowledge_source: str = "local"          # local | sharepoint
    embedding_provider: str = "local"        # local | azure_openai
    answer_provider: str = "local"           # local | azure_openai
    allow_real_provisioning: bool = False    # hard safety switch; must be true for entra_groups

    mock_directory_path: Path = REPO_ROOT / "backend" / "app" / "data" / "mock_directory.json"
    application_catalog_path: Path = REPO_ROOT / "backend" / "app" / "data" / "application_catalog.json"
    knowledge_base_path: Path = REPO_ROOT / "knowledge-base" / "sample-documents"
    auto_ingest_knowledge: bool = True

    # Microsoft Graph (client credentials) - only used by graph / entra / sharepoint adapters
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: SecretStr | None = None
    sharepoint_site_id: str | None = None
    sharepoint_drive_id: str | None = None

    # Power Automate HTTP-trigger URLs contain a SAS signature -> treat as secrets
    power_automate_approval_flow_url: SecretStr | None = None
    power_automate_notify_flow_url: SecretStr | None = None

    # Azure OpenAI (optional)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_embedding_deployment: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"

    # Secrets can alternatively be resolved from Azure Key Vault
    key_vault_url: str | None = None

    # --- workflow policy -----------------------------------------------------
    approval_timeout_hours: int = 72
    governance_approver_id: str = "u-grace"   # fallback second-stage approver
    downstream_max_attempts: int = 3
    downstream_backoff_seconds: float = 0.2

    # --- knowledge / RAG ------------------------------------------------------
    rag_top_k: int = 4
    rag_min_score: float = 0.22
    chunk_size: int = 700
    chunk_overlap: int = 120

    # --- protection ----------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 60
    idempotency_ttl_hours: int = 24

    # --- observability --------------------------------------------------------
    otel_service_name: str = "it-support-agent-api"
    otel_console_export: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    applicationinsights_connection_string: SecretStr | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.auth_mode == AuthMode.ENTRA and not (self.entra_tenant_id and self.entra_api_audience):
            raise ValueError("AUTH_MODE=entra requires ENTRA_TENANT_ID and ENTRA_API_AUDIENCE")
        if self.auth_mode == AuthMode.MOCK and self.environment == "prod":
            raise ValueError("AUTH_MODE=mock is not allowed when ENVIRONMENT=prod")
        if self.provisioning_provider == "entra_groups" and not self.allow_real_provisioning:
            raise ValueError(
                "PROVISIONING_PROVIDER=entra_groups performs real access grants; "
                "set ALLOW_REAL_PROVISIONING=true explicitly to enable it"
            )
        if self.auth_mode == AuthMode.MOCK and (self.mock_jwt_secret is None
                                               or not self.mock_jwt_secret.get_secret_value()):
            # Ephemeral per-process secret: demo tokens become invalid on restart. Never a fixed default.
            self.mock_jwt_secret = SecretStr(secrets.token_urlsafe(48))
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def callback_app_ids(self) -> set[str]:
        return {a.strip() for a in self.entra_allowed_callback_app_ids.split(",") if a.strip()}

    @property
    def is_mock_mode(self) -> bool:
        return self.auth_mode == AuthMode.MOCK


@lru_cache
def get_settings() -> Settings:
    return Settings()
