"""Shared fixtures: an isolated app (fresh SQLite file per test) and demo-persona tokens."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.observability.telemetry import metrics_registry  # noqa: E402


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", environment="test",
                log_level="WARNING", rate_limit_enabled=False, downstream_backoff_seconds=0.0,
                mock_jwt_secret="test-secret-" + "x" * 40, _env_file=None)
    base.update(overrides)
    return Settings(**base)


class Api:
    """Small helper around TestClient with per-persona auth headers."""

    def __init__(self, client: TestClient):
        self.client = client
        self._tokens: dict[str, str] = {}

    def h(self, user: str, **extra) -> dict:
        if user not in self._tokens:
            r = self.client.post("/api/v1/auth/dev-token", json={"user_id": user})
            assert r.status_code == 200, r.text
            self._tokens[user] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._tokens[user]}", **extra}

    def get(self, path, user=None, **kw):
        return self.client.get(path, headers=self.h(user) if user else kw.pop("headers", {}), **kw)

    def post(self, path, user=None, json=None, headers=None, **kw):
        hdrs = {**(self.h(user) if user else {}), **(headers or {})}
        return self.client.post(path, json=json, headers=hdrs, **kw)

    def patch(self, path, user=None, json=None):
        return self.client.patch(path, json=json, headers=self.h(user) if user else {})

    # --- domain helpers
    def submit_access(self, user, key=None, **body):
        payload = {"application_id": "salesforce-crm", "role": "read_only", "duration_days": 30,
                   "justification": "Need to review the Q4 pipeline for the finance forecast"}
        payload.update(body)
        return self.post("/api/v1/access-requests", user, payload,
                         headers={"Idempotency-Key": key or f"test-{uuid.uuid4().hex}"})

    def pending(self, user):
        return self.get("/api/v1/approvals/pending", user).json()

    def decide(self, user, approval_id, decision="approve", comment=None):
        return self.post(f"/api/v1/approvals/{approval_id}/decision", user, {"decision": decision, "comment": comment})


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics_registry.reset()
    yield


@pytest.fixture
def app_factory(tmp_path):
    def _make(**overrides):
        return create_app(make_settings(tmp_path, **overrides))
    return _make


@pytest.fixture
def app(app_factory):
    application = app_factory()
    yield application
    application.state.engine.dispose()


@pytest.fixture
def api(app) -> Api:
    return Api(TestClient(app))


@pytest.fixture
def container(app):
    return app.state.container
