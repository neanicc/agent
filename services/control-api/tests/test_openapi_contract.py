from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from loopguard_api.app import create_app
from loopguard_api.settings import Settings


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "contracts/control-api.openapi.json"
REFERENCE_PATH = ROOT / "docs/reference/control-api.md"


REQUIRED_PATHS = {
    "/v1/sessions",
    "/v1/capabilities",
    "/v1/hosts",
    "/v1/hosts/{host_id}",
    "/v1/sessions/{session_id}",
    "/v1/changes",
    "/v1/changes/{change_id}",
    "/v1/verifications",
    "/v1/verifications/{verification_id}",
    "/v1/actions",
    "/v1/actions/{action_id}",
    "/v1/repairs",
    "/v1/repairs/{repair_id}",
    "/v1/preferences",
    "/v1/devices/pairing/start",
    "/v1/devices/pairing/complete",
    "/v1/devices/{device_id}/push-token",
    "/v1/stream-tickets",
    "/v1/audit",
}


def test_openapi_contains_required_control_operations():
    paths = create_app(Settings.for_test()).openapi()["paths"]
    assert REQUIRED_PATHS <= paths.keys()


def test_checked_in_contract_and_reference_are_deterministic():
    from scripts.export_openapi import build_contract, render_reference

    expected = build_contract(build_sha="contract-test")
    checked_in = json.loads(SCHEMA_PATH.read_text())

    assert checked_in == expected
    assert REFERENCE_PATH.read_text() == render_reference(expected)
    assert checked_in["x-loopguard-contract-version"] == "1.0.0"
    assert checked_in["x-loopguard-build-sha"] == "contract-test"


def test_compatibility_checker_rejects_breaking_minor_changes():
    from scripts.export_openapi import compatibility_breaks

    baseline = {
        "paths": {
            "/v1/items": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "state"],
                    "properties": {
                        "id": {"type": "string"},
                        "state": {"type": "string", "enum": ["ready", "done"]},
                    },
                }
            }
        },
    }

    operation_removed = deepcopy(baseline)
    operation_removed["paths"] = {}
    assert compatibility_breaks(baseline, operation_removed) == ["removed operation GET /v1/items"]

    required_field_removed = deepcopy(baseline)
    required_field_removed["components"]["schemas"]["Item"]["required"].remove("state")
    assert compatibility_breaks(baseline, required_field_removed) == [
        "removed required response field Item.state"
    ]

    enum_member_removed = deepcopy(baseline)
    enum_member_removed["components"]["schemas"]["Item"]["properties"]["state"]["enum"].remove(
        "done"
    )
    assert compatibility_breaks(baseline, enum_member_removed) == [
        "removed response enum member Item.state=done"
    ]


def test_client_fixtures_cover_replay_actions_capabilities_hosts_and_repairs():
    stream = [
        json.loads(line)
        for line in (ROOT / "contracts/fixtures/session-stream.jsonl").read_text().splitlines()
    ]
    actions = json.loads((ROOT / "contracts/fixtures/action-states.json").read_text())
    capabilities = json.loads((ROOT / "contracts/fixtures/effective-capabilities.json").read_text())
    hosts = json.loads((ROOT / "contracts/fixtures/host-integration-health.json").read_text())

    assert [event["session_seq"] for event in stream] == [1, 2, 2, 4]
    assert stream[1]["event_id"] == stream[2]["event_id"]
    assert {item["state"] for item in actions} >= {
        "reviewed",
        "queued",
        "delivered",
        "executing",
        "reconciling",
        "executed",
        "rejected",
        "stale",
        "expired",
        "revoked",
    }
    assert {item["features"]["repair"]["status"] for item in capabilities} == {
        "unavailable",
        "ready",
    }
    assert {item["state"] for item in hosts} >= {
        "pending_pairing",
        "expired_pairing",
        "offline",
        "stale",
        "unregistered_repository",
        "partial_hook_coverage",
        "outdated_adapter",
        "ready",
    }
