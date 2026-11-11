"""Contract test: every operation in the Power Platform connector spec exists in the running API,
and no approval-decision endpoint is exposed to the agent."""
from pathlib import Path

import yaml

SPEC = Path(__file__).resolve().parents[3] / "copilot-studio" / "connector-openapi.yaml"


def test_connector_operations_exist_in_api(api):
    connector = yaml.safe_load(SPEC.read_text())
    api_paths = api.client.get("/openapi.json").json()["paths"]
    for path, ops in connector["paths"].items():
        assert path in api_paths, path
        for method in ops:
            assert method in api_paths[path], f"{method.upper()} {path}"


def test_connector_does_not_expose_approval_decisions():
    connector = yaml.safe_load(SPEC.read_text())
    assert not [p for p in connector["paths"] if "approval" in p]
    assert connector["swagger"] == "2.0"
