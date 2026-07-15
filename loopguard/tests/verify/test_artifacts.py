from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from loopguard.verify.artifacts import (
    ArtifactIntegrityError,
    ArtifactKeyError,
    ArtifactStore,
)
from loopguard.verify.manifest import ArtifactContext
from loopguard.verify.models import RetentionClass


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _context(
    *,
    verification_id: str = "verify-1",
    result_id: str = "result-1",
    retention: RetentionClass = RetentionClass.RAW_LOGS,
) -> ArtifactContext:
    return ArtifactContext(
        repository_id="repo-1",
        worktree_hash="a" * 64,
        verification_id=verification_id,
        command_id="unit",
        result_id=result_id,
        media_type="text/plain",
        retention_class=retention,
    )


def test_artifact_is_redacted_encrypted_hashed_and_signed(tmp_path: Path) -> None:
    store = ArtifactStore.for_test(tmp_path / "artifacts")
    secret = b"Authorization: Bearer super-secret-token"

    stored = store.put(secret, _context(), now=NOW)

    assert store.read(stored.manifest) == b"Authorization: Bearer [REDACTED]"
    assert stored.manifest.artifact_id.startswith("sha256:")
    assert stored.manifest.redaction_policy == "text-secrets-v1"
    assert stored.manifest.signature
    persisted = b"".join(path.read_bytes() for path in store.root.rglob("*") if path.is_file())
    assert secret not in persisted
    assert b"super-secret-token" not in persisted

    json_artifact = store.put(
        b'{"api_token":"json-secret"}',
        _context(result_id="json-result"),
        now=NOW,
    )
    assert store.read(json_artifact.manifest) == b'{"api_token":"[REDACTED]"}'


def test_ciphertext_hash_signature_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore.for_test(root, key=b"correct-key")
    stored = store.put(b"test output", _context(), now=NOW)
    object_path = store.object_path(stored.manifest.artifact_id)
    object_path.write_bytes(object_path.read_bytes()[:-1] + b"x")

    with pytest.raises(ArtifactIntegrityError):
        store.read(stored.manifest)

    forged = stored.manifest.model_copy(update={"signature": "0" * 64})
    with pytest.raises(ArtifactIntegrityError, match="signature"):
        store.read(forged)

    with pytest.raises(ArtifactKeyError):
        ArtifactStore.for_test(root, key=b"wrong-key")


def test_duplicate_canonical_bytes_share_one_encrypted_object_but_keep_manifests(
    tmp_path: Path,
) -> None:
    store = ArtifactStore.for_test(tmp_path / "artifacts")

    first = store.put(b"same", _context(verification_id="verify-1"), now=NOW)
    second = store.put(b"same", _context(verification_id="verify-2"), now=NOW)

    assert first.manifest.artifact_id == second.manifest.artifact_id
    assert first.manifest.manifest_id != second.manifest.manifest_id
    assert len(list((store.root / "objects").rglob("*.blob"))) == 1
    assert store.read(first.manifest) == store.read(second.manifest) == b"same"


def test_partial_publish_cleans_temporary_and_visible_objects(tmp_path: Path) -> None:
    def fail(stage: str) -> None:
        if stage == "before_publish":
            raise RuntimeError("simulated crash")

    store = ArtifactStore.for_test(tmp_path / "artifacts", fault_injector=fail)

    with pytest.raises(RuntimeError, match="simulated crash"):
        store.put(b"partial", _context(), now=NOW)

    assert not list((store.root / "objects").rglob("*.blob"))
    assert not list(store.root.rglob("*.tmp-*"))


def test_retention_expiry_is_manifest_bound_and_shred_requires_authorization(
    tmp_path: Path,
) -> None:
    store = ArtifactStore.for_test(tmp_path / "artifacts")
    stored = store.put(
        b"short lived",
        _context(retention=RetentionClass.SENSITIVE),
        now=NOW,
    )

    assert stored.manifest.expires_at == NOW + timedelta(days=1)
    with pytest.raises(PermissionError):
        store.shred(stored.manifest.artifact_id, authorized=False)
    store.shred(stored.manifest.artifact_id, authorized=True)
    with pytest.raises(ArtifactKeyError, match="shredded"):
        store.read(stored.manifest)
