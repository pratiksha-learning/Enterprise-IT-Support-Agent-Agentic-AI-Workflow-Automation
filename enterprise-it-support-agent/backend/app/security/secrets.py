"""Secret resolution.

Secrets are read from environment variables by default. When KEY_VAULT_URL is set and
``azure-identity`` + ``azure-keyvault-secrets`` are installed, ``KeyVaultSecretProvider``
resolves them with the app's managed identity (DefaultAzureCredential) - no secret is
ever stored in code or in the repository.
"""
from __future__ import annotations

import os
from typing import Protocol


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...


class EnvSecretProvider:
    def get(self, name: str) -> str | None:
        return os.environ.get(name)


class KeyVaultSecretProvider:  # pragma: no cover - requires Azure
    def __init__(self, vault_url: str):
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.keyvault.secrets import SecretClient  # type: ignore

        self._client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    def get(self, name: str) -> str | None:
        # Key Vault names cannot contain underscores: GRAPH_CLIENT_SECRET -> graph-client-secret
        try:
            return self._client.get_secret(name.lower().replace("_", "-")).value
        except Exception:
            return None


def build_secret_provider(key_vault_url: str | None) -> SecretProvider:
    if key_vault_url:
        try:
            return KeyVaultSecretProvider(key_vault_url)
        except ImportError:
            pass
    return EnvSecretProvider()
