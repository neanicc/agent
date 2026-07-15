from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .models import RetentionClass


_RETENTION_DAYS = {
    RetentionClass.SUMMARIES: 365,
    RetentionClass.RAW_LOGS: 30,
    RetentionClass.SCREENSHOTS_TRACES: 14,
    RetentionClass.SENSITIVE: 1,
}


class ArtifactContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: str = Field(min_length=1, max_length=1024)
    worktree_hash: str
    verification_id: str = Field(min_length=1, max_length=1024)
    command_id: str = Field(min_length=1, max_length=1024)
    result_id: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    retention_class: RetentionClass

    @field_validator("worktree_hash")
    @classmethod
    def validate_worktree_hash(cls, value: str) -> str:
        return _sha256(value, "worktree hash")


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str
    artifact_id: str
    sha256: str
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)
    repository_id: str = Field(min_length=1, max_length=1024)
    worktree_hash: str
    verification_id: str = Field(min_length=1, max_length=1024)
    command_id: str = Field(min_length=1, max_length=1024)
    result_id: str = Field(min_length=1, max_length=1024)
    redaction_policy: str = Field(min_length=1, max_length=255)
    encryption_key_id: str
    retention_class: RetentionClass
    created_at: AwareDatetime
    expires_at: AwareDatetime
    signature: str

    @field_validator("manifest_id", "sha256", "encryption_key_id", "signature")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _sha256(value, "manifest digest")

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        prefix, separator, digest = value.partition(":")
        if prefix != "sha256" or not separator:
            raise ValueError("artifact ID must be content-addressed")
        return f"sha256:{_sha256(digest, 'artifact digest')}"

    @field_validator("worktree_hash")
    @classmethod
    def validate_worktree_hash(cls, value: str) -> str:
        return _sha256(value, "worktree hash")


def create_manifest(
    *,
    artifact_id: str,
    content_sha256: str,
    size_bytes: int,
    context: ArtifactContext,
    redaction_policy: str,
    encryption_key_id: str,
    created_at: datetime,
    signing_key: bytes,
) -> ArtifactManifest:
    expires_at = created_at + timedelta(days=_RETENTION_DAYS[context.retention_class])
    payload = {
        "artifact_id": artifact_id,
        "sha256": content_sha256,
        "size_bytes": size_bytes,
        "media_type": context.media_type,
        "repository_id": context.repository_id,
        "worktree_hash": context.worktree_hash,
        "verification_id": context.verification_id,
        "command_id": context.command_id,
        "result_id": context.result_id,
        "redaction_policy": redaction_policy,
        "encryption_key_id": encryption_key_id,
        "retention_class": context.retention_class.value,
        "created_at": _json_datetime(created_at),
        "expires_at": _json_datetime(expires_at),
    }
    canonical = _canonical(payload)
    manifest_id = hashlib.sha256(b"loopguard-manifest-id-v1\0" + canonical).hexdigest()
    signature = hmac.new(
        signing_key,
        b"loopguard-manifest-signature-v1\0" + canonical,
        hashlib.sha256,
    ).hexdigest()
    return ArtifactManifest(
        manifest_id=manifest_id,
        signature=signature,
        **payload,
    )


def verify_manifest(manifest: ArtifactManifest, signing_key: bytes) -> None:
    payload = manifest.model_dump(mode="json", exclude={"manifest_id", "signature"})
    canonical = _canonical(payload)
    expected_id = hashlib.sha256(b"loopguard-manifest-id-v1\0" + canonical).hexdigest()
    expected_signature = hmac.new(
        signing_key,
        b"loopguard-manifest-signature-v1\0" + canonical,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(manifest.manifest_id, expected_id):
        raise ValueError("artifact manifest ID verification failed")
    if not hmac.compare_digest(manifest.signature, expected_signature):
        raise ValueError("artifact manifest signature verification failed")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _json_datetime(value: datetime) -> str:
    encoded = value.isoformat()
    return f"{encoded[:-6]}Z" if encoded.endswith("+00:00") else encoded
