from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "test", "staging", "production"]
SigningAlgorithm = Literal["Ed25519", "ES256"]
_EXTERNAL_KEY_SCHEMES = {"aws-kms", "gcp-kms", "azure-keyvault", "hsm", "test"}


class Settings(BaseSettings):
    """Validated process settings; production has no usable security defaults."""

    model_config = SettingsConfigDict(
        env_prefix="LOOPGUARD_API_",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    environment: Environment = "development"
    build_sha: str = Field(default="development", min_length=1, max_length=128)
    database_url: str = "sqlite+aiosqlite:///:memory:"
    oidc_issuer: str = "https://identity.test/"
    oidc_audience: str = "loopguard-control-api-test"
    oidc_allowed_algorithms: tuple[Literal["RS256", "ES256", "EdDSA"], ...] = (
        "RS256",
        "ES256",
        "EdDSA",
    )
    oidc_jwks_cache_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    oidc_http_timeout_seconds: float = Field(default=5, gt=0, le=30)
    action_signing_active_key_id: str = "test-action-key"
    action_signing_active_algorithm: SigningAlgorithm = "Ed25519"
    action_signing_active_key_ref: str = "test://action-current"
    action_signing_verification_key_refs: dict[str, str] = Field(default_factory=dict)
    hook_signing_policy: Literal["required", "disabled_for_test"] = "disabled_for_test"
    object_storage_bucket: str = "loopguard-test-artifacts"
    artifact_kms_key_ref: str = "test://artifact-key"
    temporal_endpoint: str = "localhost:7233"
    allowed_origins: tuple[str, ...] = ("http://localhost:3000",)
    trusted_proxy_cidrs: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ("testserver", "localhost", "127.0.0.1")
    max_request_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator(
        "build_sha",
        "database_url",
        "oidc_audience",
        "action_signing_active_key_id",
        "object_storage_bucket",
        "temporal_endpoint",
    )
    @classmethod
    def non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("setting must be non-empty and bounded")
        return normalized

    @field_validator("oidc_issuer")
    @classmethod
    def valid_oidc_issuer(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("OIDC issuer must be an exact HTTPS URL")
        return value.rstrip("/") + "/"

    @field_validator("allowed_origins")
    @classmethod
    def exact_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if (
                value == "null"
                or "*" in value
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be exact HTTP(S) origins")
            normalized.append(f"{parsed.scheme}://{parsed.netloc}")
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("CORS origins must be non-empty and unique")
        return tuple(normalized)

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def valid_proxy_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            str(ipaddress.ip_network(value, strict=True)) for value in values
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("trusted proxy networks must be unique")
        return normalized

    @field_validator("trusted_hosts")
    @classmethod
    def valid_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values)
        if (
            not normalized
            or any(not value or "*" in value or "/" in value or "\x00" in value for value in normalized)
            or len(normalized) != len(set(normalized))
        ):
            raise ValueError("trusted hosts must be exact and unique")
        return normalized

    @field_validator(
        "action_signing_active_key_ref",
        "artifact_kms_key_ref",
    )
    @classmethod
    def valid_key_reference(cls, value: str) -> str:
        return _key_reference(value)

    @field_validator("action_signing_verification_key_refs")
    @classmethod
    def valid_verification_keys(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or len(key) > 256 for key in values):
            raise ValueError("verification key IDs must be bounded")
        return {key: _key_reference(value) for key, value in values.items()}

    @model_validator(mode="after")
    def production_is_explicit(self) -> Settings:
        if self.environment != "production":
            return self
        required = {
            "database_url": self.database_url,
            "oidc_issuer": self.oidc_issuer,
            "oidc_audience": self.oidc_audience,
            "action_signing_active_key_id": self.action_signing_active_key_id,
            "action_signing_active_key_ref": self.action_signing_active_key_ref,
            "object_storage_bucket": self.object_storage_bucket,
            "artifact_kms_key_ref": self.artifact_kms_key_ref,
            "temporal_endpoint": self.temporal_endpoint,
        }
        if any("test" in value.lower() or "development" in value.lower() for value in required.values()):
            raise ValueError("production security settings must be explicit")
        if not self.database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
            raise ValueError("production requires PostgreSQL")
        if self.hook_signing_policy != "required":
            raise ValueError("production hook signing must be required")
        if not self.action_signing_verification_key_refs:
            raise ValueError("production requires verification-only signing keys")
        if any(not origin.startswith("https://") for origin in self.allowed_origins):
            raise ValueError("production browser origins must use HTTPS")
        if not self.trusted_proxy_cidrs:
            raise ValueError("production trusted proxies must be explicit")
        return self

    @classmethod
    def for_test(cls, **updates: object) -> Settings:
        return cls(environment="test", build_sha="test", **updates)


class PublicBuild(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"
    service: Literal["loopguard-control-api"] = "loopguard-control-api"
    build_sha: str
    environment: Environment


def _key_reference(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in _EXTERNAL_KEY_SCHEMES
        or not parsed.netloc
        or len(normalized) > 2_048
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("signing material must use an external key reference")
    return normalized
