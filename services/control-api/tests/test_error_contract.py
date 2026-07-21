from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.errors import ERROR_CATALOG
from loopguard_api.settings import Settings


REQUIRED = {
    "type",
    "code",
    "title",
    "detail",
    "request_id",
    "retryable",
    "doc_url",
}


def test_every_public_error_has_stable_rfc_9457_shape() -> None:
    client = TestClient(create_app(Settings.for_test()))

    response = client.get("/missing", headers={"X-Request-ID": "request_test-1"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == "request_test-1"
    assert set(response.json()) == REQUIRED
    assert response.json()["code"] == "LGAPI-NOT-FOUND"
    assert response.json()["request_id"] == "request_test-1"


def test_validation_errors_name_field_without_echoing_input() -> None:
    client = TestClient(create_app(Settings.for_test()))

    response = client.post("/_test/validate", json={"value": "must-not-echo"})

    assert response.status_code == 422
    assert response.json()["code"] == "LGAPI-REQUEST-INVALID"
    assert response.json()["field"] == "body.count"
    assert "must-not-echo" not in response.text


def test_internal_exception_is_redacted_and_request_id_is_generated() -> None:
    client = TestClient(create_app(Settings.for_test()), raise_server_exceptions=False)

    response = client.get("/_test/fail")

    assert response.status_code == 500
    assert response.json()["code"] == "LGAPI-INTERNAL"
    assert "database-password" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_cookie_authenticated_mutation_requires_matching_csrf_and_origin() -> None:
    client = TestClient(create_app(Settings.for_test()))
    client.cookies.set("session", "opaque")
    client.cookies.set("loopguard_csrf", "csrf-token")

    blocked = client.post("/_test/validate", json={"count": 1})
    allowed = client.post(
        "/_test/validate",
        json={"count": 1},
        headers={
            "Origin": "http://localhost:3000",
            "X-CSRF-Token": "csrf-token",
        },
    )

    assert blocked.status_code == 403
    assert blocked.json()["code"] == "LGAPI-CSRF-REQUIRED"
    assert allowed.status_code == 200


def test_error_catalog_and_documentation_have_one_status_per_code() -> None:
    statuses = {code: definition.status for code, definition in ERROR_CATALOG.items()}

    assert len(statuses) == len(ERROR_CATALOG)
    assert all(code.startswith("LGAPI-") for code in statuses)
    documentation = (
        Path(__file__).resolve().parents[3] / "docs/reference/control-api-errors.md"
    ).read_text()
    assert all(f"## {code}" in documentation for code in ERROR_CATALOG)


def test_untrusted_origin_proxy_and_host_fail_in_problem_format() -> None:
    client = TestClient(create_app(Settings.for_test()))

    origin = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    proxy = client.get("/health", headers={"X-Forwarded-For": "203.0.113.8"})
    host = client.get("/health", headers={"Host": "attacker.example"})

    assert origin.json()["code"] == "LGAPI-ORIGIN-DENIED"
    assert proxy.json()["code"] == "LGAPI-PROXY-UNTRUSTED"
    assert host.json()["code"] == "LGAPI-HOST-UNTRUSTED"
    assert all(
        response.headers["content-type"].startswith("application/problem+json")
        for response in (origin, proxy, host)
    )


def test_body_limit_and_invalid_request_id_are_enforced_at_boundary() -> None:
    settings = Settings.for_test(max_request_bytes=1024)
    client = TestClient(create_app(settings))

    response = client.post(
        "/_test/validate",
        content=b"x" * 1025,
        headers={"content-type": "application/json", "X-Request-ID": "invalid id"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "LGAPI-BODY-TOO-LARGE"
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-request-id"] != "invalid id"
