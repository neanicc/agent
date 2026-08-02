from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from loopguard_api.artifacts import (
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactService,
    MemoryKms,
    MemoryObjectStore,
    RetentionWorker,
)


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _service(*, clock=lambda: NOW):
    objects = MemoryObjectStore()
    service = ArtifactService(objects=objects, kms=MemoryKms(), clock=clock)
    return service, objects


def _completed(service: ArtifactService, objects: MemoryObjectStore, tenant_id: uuid.UUID):
    body = b"verified evidence"
    artifact, upload = service.initiate(
        tenant_id=tenant_id,
        declared_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        media_type="application/json",
        retention_class="proof",
        expires_at=NOW + timedelta(days=30),
    )
    objects.upload(upload, body, content_type="application/json")
    return service.complete(
        tenant_id=tenant_id,
        artifact_id=artifact.id,
        evidence_manifest=service.sign_manifest_for_test(artifact.id),
    )


def test_artifact_download_is_tenant_scoped():
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    service, objects = _service()
    artifact = _completed(service, objects, tenant_a)

    with pytest.raises(ArtifactNotFound):
        service.download(tenant_id=tenant_b, artifact_id=artifact.id)
    assert service.download(tenant_id=tenant_a, artifact_id=artifact.id).startswith(
        "https://objects.example.test/"
    )


def test_completion_checks_sha256_size_and_content_type_not_etag():
    tenant_id = uuid.uuid4()
    service, objects = _service()
    body = b"correct"
    artifact, upload = service.initiate(
        tenant_id=tenant_id,
        declared_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        media_type="application/json",
        retention_class="proof",
        expires_at=NOW + timedelta(days=1),
    )
    objects.upload(
        upload,
        b"wrong!!",
        content_type="application/json",
        etag="multipart-etag-2",
    )

    with pytest.raises(ArtifactIntegrityError, match="checksum"):
        service.complete(
            tenant_id=tenant_id,
            artifact_id=artifact.id,
            evidence_manifest=service.sign_manifest_for_test(artifact.id),
        )


def test_provider_without_checksum_recomputes_bounded_stream():
    tenant_id = uuid.uuid4()
    service, objects = _service()
    body = b"stream me"
    artifact, upload = service.initiate(
        tenant_id=tenant_id,
        declared_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        media_type="text/plain",
        retention_class="log",
        expires_at=NOW + timedelta(days=1),
    )
    objects.upload(upload, body, content_type="text/plain", omit_checksum=True)

    completed = service.complete(
        tenant_id=tenant_id,
        artifact_id=artifact.id,
        evidence_manifest=service.sign_manifest_for_test(artifact.id),
    )

    assert completed.state == "complete"


def test_expired_artifact_is_deleted_and_failures_retry():
    current = NOW
    service, objects = _service(clock=lambda: current)
    artifact = _completed(service, objects, uuid.uuid4())
    current += timedelta(days=31)
    objects.fail_next_delete = True
    worker = RetentionWorker(service)

    assert worker.run_once() == 0
    assert objects.exists(artifact.object_key)
    assert worker.run_once() == 1
    assert not objects.exists(artifact.object_key)


def test_legal_hold_overrides_expiry():
    current = NOW
    service, objects = _service(clock=lambda: current)
    artifact = _completed(service, objects, uuid.uuid4())
    service.set_legal_hold(artifact.id, enabled=True, actor="legal-admin")
    current += timedelta(days=31)

    assert RetentionWorker(service).run_once() == 0
    assert objects.exists(artifact.object_key)
