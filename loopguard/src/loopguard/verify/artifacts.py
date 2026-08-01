from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loopguard.control.crypto import (
    IntegrityError,
    decrypt,
    encrypt,
    generate_data_key,
    key_id_for,
)

from .manifest import (
    ArtifactContext,
    ArtifactManifest,
    create_manifest,
    verify_manifest,
)


_BLOB_HEADER = b"LGAB1"
_KEY_HEADER = b"LGAK1"
_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+")
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*\s*[=:]\s*)"
    r"([^\s,;]+)"
)
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JSON_SECRET = re.compile(
    r'(?i)("[^"\\]*(?:token|secret|password|private_key|access_key)[^"\\]*"\s*:\s*")'
    r'([^"\\]*)(")'
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class ArtifactError(RuntimeError):
    """Base class for immutable verification artifact failures."""


class ArtifactIntegrityError(ArtifactError):
    """Stored bytes or signed metadata failed integrity verification."""


class ArtifactKeyError(ArtifactError):
    """Artifact encryption key material is wrong, missing, or shredded."""


class ArtifactUnavailableError(ArtifactError):
    """A referenced artifact object is not locally available."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    manifest: ArtifactManifest


class ArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        key_material: bytes,
        max_artifact_bytes: int = 256 * 1024 * 1024,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if not key_material:
            raise ValueError("artifact key material must not be empty")
        if max_artifact_bytes <= 0:
            raise ValueError("artifact size limit must be positive")
        requested_root = root.expanduser().absolute()
        if requested_root.is_symlink():
            raise ArtifactIntegrityError("artifact root cannot be a symlink")
        self.root = requested_root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(mode=0o700, exist_ok=True)
        self.objects.chmod(0o700)
        self._master_key = hashlib.sha256(
            b"loopguard-verify-artifact-master-v1\0" + key_material
        ).digest()
        self._signing_key = hashlib.sha256(
            b"loopguard-verify-manifest-signing-v1\0" + key_material
        ).digest()
        self._master_key_id = key_id_for(self._master_key)
        self.max_artifact_bytes = max_artifact_bytes
        self.fault_injector = fault_injector
        self._lock = threading.RLock()
        self._load_or_create_metadata()

    @classmethod
    def for_test(
        cls,
        root: Path,
        *,
        key: bytes = b"loopguard-verification-test-key",
        fault_injector: Callable[[str], None] | None = None,
    ) -> ArtifactStore:
        return cls(root, key_material=key, fault_injector=fault_injector)

    def put(
        self,
        raw: bytes,
        context: ArtifactContext,
        *,
        now,
    ) -> StoredArtifact:
        with self._lock:
            return self._put(raw, context, now=now)

    def _put(
        self,
        raw: bytes,
        context: ArtifactContext,
        *,
        now,
    ) -> StoredArtifact:
        if not isinstance(raw, bytes):
            raise TypeError("artifact bytes must be immutable bytes")
        if len(raw) > self.max_artifact_bytes:
            raise ValueError("artifact exceeds configured size limit")
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("artifact creation time must be timezone-aware")
        canonical, redaction_policy = _canonicalize(raw, context.media_type)
        digest = hashlib.sha256(canonical).hexdigest()
        artifact_id = f"sha256:{digest}"
        blob_path = self.object_path(artifact_id)
        key_path = self.key_path(artifact_id)
        if blob_path.exists() or key_path.exists():
            if (
                blob_path.is_symlink()
                or key_path.is_symlink()
                or not blob_path.is_file()
                or not key_path.is_file()
            ):
                raise ArtifactIntegrityError("artifact object is only partially present")
            existing = self._read_object(artifact_id, blob_path, key_path)
            if existing != canonical:
                raise ArtifactIntegrityError("content-addressed artifact bytes do not match")
            data_key_id = _read_key_id(key_path, self._master_key, artifact_id)
        else:
            data_key = generate_data_key()
            data_key_id = key_id_for(data_key)
            blob = _pack(
                _BLOB_HEADER,
                *encrypt(data_key, canonical, _artifact_aad(artifact_id)),
            )
            wrapped_key = _pack(
                _KEY_HEADER,
                *encrypt(self._master_key, data_key, _key_aad(artifact_id)),
            )
            self._publish_pair(blob_path, blob, key_path, wrapped_key)
        manifest = create_manifest(
            artifact_id=artifact_id,
            content_sha256=digest,
            size_bytes=len(canonical),
            context=context,
            redaction_policy=redaction_policy,
            encryption_key_id=data_key_id,
            created_at=now,
            signing_key=self._signing_key,
        )
        return StoredArtifact(manifest=manifest)

    def read(self, manifest: ArtifactManifest) -> bytes:
        try:
            verify_manifest(manifest, self._signing_key)
        except ValueError as exc:
            raise ArtifactIntegrityError(str(exc)) from exc
        blob_path = self.object_path(manifest.artifact_id)
        key_path = self.key_path(manifest.artifact_id)
        if not blob_path.is_file():
            raise ArtifactUnavailableError("artifact object is unavailable")
        if blob_path.is_symlink() or key_path.is_symlink():
            raise ArtifactIntegrityError("artifact object cannot be a symlink")
        if not key_path.is_file():
            raise ArtifactKeyError("artifact key was shredded or is unavailable")
        canonical = self._read_object(manifest.artifact_id, blob_path, key_path)
        digest = hashlib.sha256(canonical).hexdigest()
        if digest != manifest.sha256 or len(canonical) != manifest.size_bytes:
            raise ArtifactIntegrityError("artifact content hash or size does not match manifest")
        data_key_id = _read_key_id(key_path, self._master_key, manifest.artifact_id)
        if data_key_id != manifest.encryption_key_id:
            raise ArtifactIntegrityError("artifact encryption key does not match manifest")
        return canonical

    def object_path(self, artifact_id: str) -> Path:
        digest = _artifact_digest(artifact_id)
        return self.objects / digest[:2] / f"{digest}.blob"

    def key_path(self, artifact_id: str) -> Path:
        digest = _artifact_digest(artifact_id)
        return self.objects / digest[:2] / f"{digest}.key"

    def shred(self, artifact_id: str, *, authorized: bool) -> None:
        if not authorized:
            raise PermissionError("artifact deletion requires privileged authorization")
        key_path = self.key_path(artifact_id)
        key_path.unlink(missing_ok=True)
        if key_path.parent.is_dir():
            _fsync_directory(key_path.parent)

    def remove_object(self, artifact_id: str) -> None:
        object_path = self.object_path(artifact_id)
        object_path.unlink(missing_ok=True)
        self.key_path(artifact_id).unlink(missing_ok=True)
        if object_path.parent.is_dir():
            _fsync_directory(object_path.parent)

    def prune_unreferenced(self, referenced_artifact_ids: Iterable[str]) -> list[str]:
        referenced = set(referenced_artifact_ids)
        removed: list[str] = []
        for blob in self.objects.rglob("*.blob"):
            artifact_id = f"sha256:{blob.stem}"
            if artifact_id not in referenced:
                self.remove_object(artifact_id)
                removed.append(artifact_id)
        for key in self.objects.rglob("*.key"):
            artifact_id = f"sha256:{key.stem}"
            if artifact_id not in referenced:
                key.unlink(missing_ok=True)
        return sorted(removed)

    def _read_object(self, artifact_id: str, blob_path: Path, key_path: Path) -> bytes:
        try:
            key_nonce, wrapped_key = _unpack(key_path.read_bytes(), _KEY_HEADER)
            data_key = decrypt(
                self._master_key,
                key_nonce,
                wrapped_key,
                _key_aad(artifact_id),
            )
            blob_nonce, ciphertext = _unpack(blob_path.read_bytes(), _BLOB_HEADER)
            return decrypt(data_key, blob_nonce, ciphertext, _artifact_aad(artifact_id))
        except (OSError, IntegrityError, ValueError) as exc:
            raise ArtifactIntegrityError("artifact authenticated decryption failed") from exc

    def _publish_pair(
        self,
        blob_path: Path,
        blob: bytes,
        key_path: Path,
        wrapped_key: bytes,
    ) -> None:
        blob_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if blob_path.parent.is_symlink():
            raise ArtifactIntegrityError("artifact shard cannot be a symlink")
        temporary: list[Path] = []
        published: list[Path] = []
        try:
            blob_temp = _write_temp(blob_path.parent, blob_path.name, blob)
            key_temp = _write_temp(key_path.parent, key_path.name, wrapped_key)
            temporary.extend((blob_temp, key_temp))
            self._fault("before_publish")
            os.replace(key_temp, key_path)
            temporary.remove(key_temp)
            published.append(key_path)
            self._fault("after_key_publish")
            os.replace(blob_temp, blob_path)
            temporary.remove(blob_temp)
            published.append(blob_path)
            _fsync_directory(blob_path.parent)
        except Exception:
            for path in published:
                path.unlink(missing_ok=True)
            raise
        finally:
            for path in temporary:
                path.unlink(missing_ok=True)

    def _fault(self, stage: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(stage)

    def _load_or_create_metadata(self) -> None:
        path = self.root / "metadata.json"
        if path.is_symlink():
            raise ArtifactIntegrityError("artifact metadata cannot be a symlink")
        if path.exists():
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ArtifactIntegrityError("artifact metadata is invalid") from exc
            if payload != {"key_id": self._master_key_id, "version": 1}:
                raise ArtifactKeyError("artifact store key does not match existing data")
            return
        temporary = _write_temp(
            self.root,
            path.name,
            json.dumps(
                {"key_id": self._master_key_id, "version": 1},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        os.replace(temporary, path)
        _fsync_directory(self.root)


def _canonicalize(raw: bytes, media_type: str) -> tuple[bytes, str]:
    normalized_media_type = media_type.partition(";")[0].strip().lower()
    is_text = normalized_media_type.startswith("text/") or normalized_media_type in {
        "application/json",
        "application/javascript",
        "application/xml",
    }
    if not is_text:
        return raw, "binary-classified-v1"
    text = raw.decode("utf-8", errors="replace")
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = _JSON_SECRET.sub(r"\1[REDACTED]\3", text)
    text = _AWS_KEY.sub("[REDACTED]", text)
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    return text.encode(), "text-secrets-v1"


def _artifact_digest(artifact_id: str) -> str:
    prefix, separator, digest = artifact_id.partition(":")
    if prefix != "sha256" or not separator or len(digest) != 64:
        raise ValueError("artifact ID must be a SHA-256 content address")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("artifact ID must be lowercase hexadecimal")
    return digest


def _artifact_aad(artifact_id: str) -> bytes:
    return b"loopguard-verify-artifact-object-v1\0" + artifact_id.encode()


def _key_aad(artifact_id: str) -> bytes:
    return b"loopguard-verify-artifact-key-v1\0" + artifact_id.encode()


def _pack(header: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return header + nonce + ciphertext


def _unpack(payload: bytes, header: bytes) -> tuple[bytes, bytes]:
    if not payload.startswith(header) or len(payload) < len(header) + 12 + 16:
        raise ValueError("encrypted artifact object has an invalid envelope")
    offset = len(header)
    return payload[offset : offset + 12], payload[offset + 12 :]


def _read_key_id(key_path: Path, master_key: bytes, artifact_id: str) -> str:
    try:
        nonce, ciphertext = _unpack(key_path.read_bytes(), _KEY_HEADER)
        data_key = decrypt(master_key, nonce, ciphertext, _key_aad(artifact_id))
    except (OSError, IntegrityError, ValueError) as exc:
        raise ArtifactIntegrityError("artifact key envelope failed authentication") from exc
    return key_id_for(data_key)


def _write_temp(directory: Path, name: str, payload: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{name}.tmp-", dir=directory)
    path = Path(raw_path)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
