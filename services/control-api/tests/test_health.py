from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from loopguard_api.app import create_app
from loopguard_api.settings import Settings


def test_health_exposes_build_not_secrets() -> None:
    settings = Settings.for_test()
    body = TestClient(create_app(settings)).get("/health").json()

    assert body == {
        "status": "ok",
        "service": "loopguard-control-api",
        "build_sha": "test",
        "environment": "test",
    }
    encoded = str(body)
    assert settings.database_url not in encoded
    assert settings.action_signing_active_key_ref not in encoded


def test_production_rejects_defaults_and_incomplete_security_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", action_signing_active_key_ref="development")


def test_production_accepts_only_external_key_references_and_exact_origins() -> None:
    settings = Settings(
        environment="production",
        build_sha="a" * 40,
        database_url="postgresql+asyncpg://control@db.internal/loopguard",
        oidc_issuer="https://identity.example.com/",
        oidc_audience="loopguard-control-api",
        action_signing_active_key_id="action-2026-07",
        action_signing_active_algorithm="Ed25519",
        action_signing_active_key_ref="aws-kms://us-east-1/key/action-current",
        action_signing_verification_key_refs={
            "action-2026-06": "aws-kms://us-east-1/key/action-retiring"
        },
        hook_signing_policy="required",
        object_storage_bucket="loopguard-production-artifacts",
        artifact_kms_key_ref="aws-kms://us-east-1/key/artifacts",
        temporal_endpoint="temporal.internal:7233",
        allowed_origins=("https://app.loopguard.example",),
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )

    assert settings.environment == "production"
    assert settings.allowed_origins == ("https://app.loopguard.example",)
    payload = settings.model_dump()
    payload["action_signing_active_key_ref"] = "raw-private-key"
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


@pytest.mark.parametrize(
    "origin",
    ["*", "https://*.example.com", "https://user:password@example.com", "null"],
)
def test_origins_are_exact_and_never_credentialed_wildcards(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings.for_test(allowed_origins=(origin,))
