from __future__ import annotations

from pathlib import Path

from loopguard_api.app import create_app
from loopguard_api.settings import Settings


REQUIRED_OPERATIONS = {
    ("get", "/v1/capabilities"),
    ("get", "/v1/hosts"),
    ("get", "/v1/hosts/{host_id}"),
    ("get", "/v1/sessions"),
    ("get", "/v1/sessions/{session_id}"),
    ("get", "/v1/changes"),
    ("get", "/v1/changes/{change_id}"),
    ("get", "/v1/verifications"),
    ("get", "/v1/verifications/{verification_id}"),
    ("get", "/v1/actions"),
    ("get", "/v1/actions/{action_id}"),
    ("post", "/v1/actions/challenge"),
    ("post", "/v1/actions"),
    ("get", "/v1/repairs"),
    ("get", "/v1/repairs/{repair_id}"),
    ("post", "/v1/artifacts"),
    ("post", "/v1/artifacts/{artifact_id}/complete"),
    ("get", "/v1/artifacts/{artifact_id}"),
    ("get", "/v1/artifacts/{artifact_id}/download"),
    ("get", "/v1/preferences"),
    ("put", "/v1/preferences"),
    ("get", "/v1/costs"),
    ("get", "/v1/devices"),
    ("post", "/v1/devices/pairing/start"),
    ("post", "/v1/devices/pairing/complete"),
    ("put", "/v1/devices/{device_id}/push-token"),
    ("delete", "/v1/devices/{device_id}"),
    ("post", "/v1/stream-tickets"),
    ("get", "/v1/audit"),
}


def test_openapi_contains_every_owned_client_operation_and_unique_ids():
    schema = create_app(Settings.for_test()).openapi()
    actual = {
        (method, path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    operation_ids = [
        operation["operationId"]
        for operations in schema["paths"].values()
        for method, operation in operations.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]

    assert REQUIRED_OPERATIONS <= actual
    assert len(operation_ids) == len(set(operation_ids))


def test_endpoint_contract_names_every_openapi_operation():
    schema = create_app(Settings.for_test()).openapi()
    contract = (
        Path(__file__).resolve().parents[3] / "contracts/control-api-endpoints.md"
    ).read_text()

    for method, path in REQUIRED_OPERATIONS:
        assert f"`{method.upper()} {path}`" in contract
        assert path in schema["paths"]
