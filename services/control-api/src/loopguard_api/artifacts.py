from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from urllib.parse import quote


class ArtifactError(ValueError):
    pass


class ArtifactNotFound(ArtifactError):
    pass


class ArtifactIntegrityError(ArtifactError):
    pass


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    object_key: str
    required_sha256: str
    required_byte_count: int
    required_media_type: str
    expires_at: datetime
    encryption_metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    byte_count: int
    content_type: str
    checksum_sha256: str | None
    etag: str | None


class ObjectStore(Protocol):
    def presign_upload(self, upload: PresignedUpload) -> PresignedUpload: ...
    def head(self, object_key: str) -> ObjectMetadata | None: ...
    def read_bounded(self, object_key: str, maximum_bytes: int) -> bytes: ...
    def presign_download(self, object_key: str, expires_in: int) -> str: ...
    def delete(self, object_key: str) -> None: ...


class Kms(Protocol):
    def envelope_metadata(self, tenant_id: uuid.UUID, artifact_id: uuid.UUID) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    object_key: str
    sha256: str
    byte_count: int
    media_type: str
    encryption_metadata: dict[str, str]
    retention_class: str
    expires_at: datetime | None
    state: str
    legal_hold: bool = False
    delete_attempts: int = 0


class ArtifactService:
    def __init__(
        self,
        *,
        objects: ObjectStore,
        kms: Kms,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        manifest_verification_key: bytes | None = None,
        maximum_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        if maximum_bytes < 1 or maximum_bytes > 5 * 1024 * 1024 * 1024:
            raise ValueError("artifact byte limit is invalid")
        self.objects = objects
        self.kms = kms
        self._clock = clock
        self._manifest_key = manifest_verification_key or secrets.token_bytes(32)
        self.maximum_bytes = maximum_bytes
        self._records: dict[uuid.UUID, ArtifactRecord] = {}
        self._audit: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def initiate(
        self,
        *,
        tenant_id: uuid.UUID,
        declared_sha256: str,
        byte_count: int,
        media_type: str,
        retention_class: str,
        expires_at: datetime | None,
    ) -> tuple[ArtifactRecord, PresignedUpload]:
        if (
            len(declared_sha256) != 64
            or any(character not in "0123456789abcdef" for character in declared_sha256)
            or byte_count < 1
            or byte_count > self.maximum_bytes
            or not media_type
            or retention_class not in {"source", "log", "proof", "repair"}
        ):
            raise ArtifactIntegrityError("artifact declaration is invalid")
        artifact_id = uuid.uuid4()
        object_key = f"tenants/{tenant_id}/artifacts/{artifact_id}"
        encryption = self.kms.envelope_metadata(tenant_id, artifact_id)
        record = ArtifactRecord(
            id=artifact_id,
            tenant_id=tenant_id,
            object_key=object_key,
            sha256=declared_sha256,
            byte_count=byte_count,
            media_type=media_type,
            encryption_metadata=encryption,
            retention_class=retention_class,
            expires_at=expires_at,
            state="pending",
        )
        upload = PresignedUpload(
            url=f"https://objects.example.test/upload/{quote(object_key, safe='')}",
            object_key=object_key,
            required_sha256=declared_sha256,
            required_byte_count=byte_count,
            required_media_type=media_type,
            expires_at=self._clock() + timedelta(minutes=10),
            encryption_metadata=encryption,
        )
        with self._lock:
            self._records[artifact_id] = record
        return record, self.objects.presign_upload(upload)

    def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_id: uuid.UUID,
        evidence_manifest: str,
    ) -> ArtifactRecord:
        with self._lock:
            record = self._tenant_record(tenant_id, artifact_id)
            if record.state == "complete":
                return record
            if record.expires_at is not None and self._clock() > record.expires_at:
                try:
                    self.objects.delete(record.object_key)
                finally:
                    self._records[artifact_id] = replace(record, state="expired")
                raise ArtifactIntegrityError("artifact upload expired")
            if not self._verify_manifest(artifact_id, evidence_manifest):
                raise ArtifactIntegrityError("evidence manifest signature is invalid")
            metadata = self.objects.head(record.object_key)
            if metadata is None:
                raise ArtifactIntegrityError("artifact object is not available")
            if metadata.byte_count != record.byte_count:
                raise ArtifactIntegrityError("artifact size does not match declaration")
            if metadata.content_type != record.media_type:
                raise ArtifactIntegrityError("artifact content type does not match declaration")
            checksum = metadata.checksum_sha256
            if checksum is None:
                body = self.objects.read_bounded(
                    record.object_key, record.byte_count + 1
                )
                if len(body) != record.byte_count:
                    raise ArtifactIntegrityError("artifact size does not match declaration")
                checksum = hashlib.sha256(body).hexdigest()
            if not hmac.compare_digest(checksum, record.sha256):
                raise ArtifactIntegrityError("artifact checksum does not match declaration")
            complete = replace(record, state="complete")
            self._records[artifact_id] = complete
            self._audit.append(
                {"action": "artifact.complete", "artifact_id": str(artifact_id)}
            )
            return complete

    def download(self, *, tenant_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
        with self._lock:
            record = self._tenant_record(tenant_id, artifact_id)
            if record.state != "complete" or (
                record.expires_at is not None and self._clock() > record.expires_at
            ):
                raise ArtifactNotFound("artifact not found")
            self._audit.append(
                {"action": "artifact.download", "artifact_id": str(artifact_id)}
            )
            return self.objects.presign_download(record.object_key, expires_in=60)

    def metadata(
        self, *, tenant_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> ArtifactRecord:
        with self._lock:
            return self._tenant_record(tenant_id, artifact_id)

    def set_legal_hold(self, artifact_id: uuid.UUID, *, enabled: bool, actor: str) -> None:
        with self._lock:
            record = self._records[artifact_id]
            self._records[artifact_id] = replace(record, legal_hold=enabled)
            self._audit.append(
                {
                    "action": "artifact.legal_hold",
                    "artifact_id": str(artifact_id),
                    "actor": actor,
                    "enabled": str(enabled).lower(),
                }
            )

    def delete_expired(self) -> int:
        deleted = 0
        now = self._clock()
        with self._lock:
            candidates = list(self._records.values())
            for record in candidates:
                if (
                    record.expires_at is None
                    or record.expires_at >= now
                    or record.legal_hold
                    or record.state == "deleted"
                ):
                    continue
                try:
                    self.objects.delete(record.object_key)
                except Exception:
                    self._records[record.id] = replace(
                        record,
                        state="delete_failed",
                        delete_attempts=record.delete_attempts + 1,
                    )
                    continue
                self._records[record.id] = replace(
                    record,
                    state="deleted",
                    delete_attempts=record.delete_attempts + 1,
                )
                self._audit.append(
                    {"action": "artifact.deleted", "artifact_id": str(record.id)}
                )
                deleted += 1
        return deleted

    def sign_manifest_for_test(self, artifact_id: uuid.UUID) -> str:
        return hmac.new(
            self._manifest_key, str(artifact_id).encode(), hashlib.sha256
        ).hexdigest()

    def _verify_manifest(self, artifact_id: uuid.UUID, signature: str) -> bool:
        return hmac.compare_digest(self.sign_manifest_for_test(artifact_id), signature)

    def _tenant_record(
        self, tenant_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> ArtifactRecord:
        record = self._records.get(artifact_id)
        if record is None or record.tenant_id != tenant_id:
            raise ArtifactNotFound("artifact not found")
        return record


class RetentionWorker:
    def __init__(self, service: ArtifactService) -> None:
        self.service = service

    def run_once(self) -> int:
        return self.service.delete_expired()


class MemoryKms:
    def envelope_metadata(
        self, tenant_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> dict[str, str]:
        return {
            "algorithm": "AES-256-GCM",
            "kms_key_id": "test-kms-key",
            "encrypted_data_key": hashlib.sha256(
                f"{tenant_id}:{artifact_id}".encode()
            ).hexdigest(),
        }


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, ObjectMetadata]] = {}
        self.fail_next_delete = False

    def presign_upload(self, upload: PresignedUpload) -> PresignedUpload:
        return upload

    def upload(
        self,
        upload: PresignedUpload,
        body: bytes,
        *,
        content_type: str,
        etag: str | None = None,
        omit_checksum: bool = False,
    ) -> None:
        metadata = ObjectMetadata(
            byte_count=len(body),
            content_type=content_type,
            checksum_sha256=None if omit_checksum else hashlib.sha256(body).hexdigest(),
            etag=etag,
        )
        self._objects[upload.object_key] = (body, metadata)

    def head(self, object_key: str) -> ObjectMetadata | None:
        value = self._objects.get(object_key)
        return None if value is None else value[1]

    def read_bounded(self, object_key: str, maximum_bytes: int) -> bytes:
        return self._objects[object_key][0][:maximum_bytes]

    def presign_download(self, object_key: str, expires_in: int) -> str:
        return (
            f"https://objects.example.test/download/{quote(object_key, safe='')}"
            f"?expires_in={expires_in}"
        )

    def delete(self, object_key: str) -> None:
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("injected object-store deletion failure")
        self._objects.pop(object_key, None)

    def exists(self, object_key: str) -> bool:
        return object_key in self._objects
