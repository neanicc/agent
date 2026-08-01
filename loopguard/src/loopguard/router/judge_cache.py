from __future__ import annotations

import json
import os
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from secrets import token_hex

from pydantic import BaseModel, ConfigDict, Field, field_validator

from loopguard.control.paths import ensure_private_home
from loopguard.control.redaction import redact
from loopguard.config import LoopGuardConfig
from loopguard.event import LoopEvent
from loopguard.judge import JudgeVerdict
from loopguard.normalize import normalize_event


_AAD = b"loopguard-judge-incident-cache-v1"
_HEADER = b"LGJC1"
_MAX_FILE_BYTES = 2_000_000


class CachePersistenceError(RuntimeError):
    """The encrypted incident cache could not be read or written safely."""


class IncidentCacheKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=128)
    detector_kind: str = Field(min_length=1, max_length=128)
    incident_fingerprint: str = Field(min_length=64, max_length=64)
    judge_model: str = Field(min_length=1, max_length=256)
    prompt_version: str = Field(min_length=1, max_length=128)

    @field_validator("incident_fingerprint")
    @classmethod
    def _fingerprint_is_hex(cls, value: str) -> str:
        normalized = value.lower()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("incident fingerprint must be SHA-256 hex")
        return normalized


@dataclass(frozen=True, slots=True)
class CacheResult:
    verdict: JudgeVerdict
    computed: bool
    cache_hit: bool


@dataclass(slots=True)
class _Entry:
    verdict: JudgeVerdict
    expires_at: float


@dataclass(slots=True)
class _Flight:
    event: threading.Event
    verdict: JudgeVerdict | None = None
    error: BaseException | None = None


class JudgeIncidentCache:
    """Bounded validated-outcome cache with cross-run identity and single-flight calls."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300,
        max_entries: int = 512,
        clock: Callable[[], float] = time.time,
        path: str | Path | None = None,
        encryption_key: bytes | None = None,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > 86_400:
            raise ValueError("judge cache TTL must be within one day")
        if max_entries <= 0 or max_entries > 100_000:
            raise ValueError("judge cache entry limit is invalid")
        if (path is None) != (encryption_key is None):
            raise ValueError("persistent judge cache requires both path and encryption key")
        if encryption_key is not None and len(encryption_key) != 32:
            raise ValueError("judge cache encryption key must contain 32 bytes")
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = max_entries
        self._clock = clock
        self._path = Path(path) if path is not None else None
        self._encryption_key = encryption_key
        self._entries: OrderedDict[IncidentCacheKey, _Entry] = OrderedDict()
        self._flights: dict[IncidentCacheKey, _Flight] = {}
        self._lock = threading.RLock()
        if self._path is not None:
            self._load()

    def get_or_compute(
        self,
        key: IncidentCacheKey,
        compute: Callable[[], JudgeVerdict],
    ) -> CacheResult:
        with self._lock:
            self._purge_expired_locked()
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return CacheResult(entry.verdict.model_copy(deep=True), False, True)
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = _Flight(event=threading.Event())
                self._flights[key] = flight
        assert flight is not None
        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.verdict is not None
            return CacheResult(flight.verdict.model_copy(deep=True), False, False)
        try:
            verdict = compute()
            if not isinstance(verdict, JudgeVerdict):
                raise TypeError("judge cache compute callback must return JudgeVerdict")
            with self._lock:
                if verdict.validated:
                    previous_entries = OrderedDict(self._entries)
                    self._entries[key] = _Entry(
                        verdict=verdict.model_copy(deep=True),
                        expires_at=self._clock() + self.ttl_seconds,
                    )
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
                    try:
                        self._persist_locked()
                    except Exception:
                        self._entries = previous_entries
                        raise
                flight.verdict = verdict.model_copy(deep=True)
                flight.event.set()
                self._flights.pop(key, None)
            return CacheResult(verdict, True, False)
        except BaseException as exc:
            with self._lock:
                flight.error = exc
                flight.event.set()
                self._flights.pop(key, None)
            raise

    def clear(self) -> None:
        """Clear validated outcomes; in-flight calls continue for their current waiters."""
        with self._lock:
            self._entries.clear()
            self._persist_locked()

    def _purge_expired_locked(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    def _load(self) -> None:
        assert self._path is not None and self._encryption_key is not None
        ensure_private_home(self._path.parent)
        try:
            status = os.lstat(self._path)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise CachePersistenceError("judge cache path is not a regular file")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise CachePersistenceError("judge cache file must be owner-only")
        if status.st_size > _MAX_FILE_BYTES:
            raise CachePersistenceError("judge cache file exceeds its size limit")
        try:
            encoded = _read_private_file(self._path)
            if not encoded.startswith(_HEADER) or len(encoded) < len(_HEADER) + 12 + 16:
                raise ValueError("invalid header")
            nonce = encoded[len(_HEADER) : len(_HEADER) + 12]
            ciphertext = encoded[len(_HEADER) + 12 :]
            payload = _aesgcm(self._encryption_key).decrypt(nonce, ciphertext, _AAD)
            records = json.loads(payload)
            if not isinstance(records, list) or len(records) > self.max_entries:
                raise ValueError("invalid record set")
            now = self._clock()
            for record in records:
                key = IncidentCacheKey.model_validate(record["key"])
                verdict = JudgeVerdict.model_validate(record["verdict"])
                expires_at = float(record["expires_at"])
                if verdict.validated and expires_at > now:
                    self._entries[key] = _Entry(verdict, expires_at)
        except Exception as exc:
            raise CachePersistenceError("encrypted judge cache failed validation") from exc

    def _persist_locked(self) -> None:
        if self._path is None or self._encryption_key is None:
            return
        ensure_private_home(self._path.parent)
        _validate_existing_target(self._path)
        records = [
            {
                "key": key.model_dump(mode="json"),
                "verdict": entry.verdict.model_dump(mode="json"),
                "expires_at": entry.expires_at,
            }
            for key, entry in self._entries.items()
        ]
        plaintext = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        nonce = os.urandom(12)
        encoded = _HEADER + nonce + _aesgcm(self._encryption_key).encrypt(nonce, plaintext, _AAD)
        if len(encoded) > _MAX_FILE_BYTES:
            raise CachePersistenceError("encrypted judge cache exceeds its size limit")
        temporary = self._path.with_name(f".{self._path.name}.{token_hex(8)}.tmp")
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(temporary, flags, 0o600)
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, self._path)
            _sync_directory(self._path.parent)
        finally:
            if temporary.exists():
                temporary.unlink()


def incident_cache_key(
    *,
    policy_version: str,
    detector_kind: str,
    events: Sequence[LoopEvent],
    judge_model: str,
    prompt_version: str,
    task: str | None = None,
    context: str | None = None,
    normalization_config: LoopGuardConfig | None = None,
) -> IncidentCacheKey:
    bounded_events = list(events[-64:])
    payload = {
        "events": [normalize_event(event, normalization_config) for event in bounded_events],
        "task": _bounded_redacted_text(task),
        "context": _bounded_redacted_text(context),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return IncidentCacheKey(
        policy_version=policy_version,
        detector_kind=detector_kind,
        incident_fingerprint=sha256(canonical.encode()).hexdigest(),
        judge_model=judge_model,
        prompt_version=prompt_version,
    )


def _bounded_redacted_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(redact(value[:20_000])).lower().split())


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise CachePersistenceError(
            "persistent judge cache requires the LoopGuard control dependency"
        ) from exc
    return AESGCM(key)


def _validate_existing_target(path: Path) -> None:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
        raise CachePersistenceError("judge cache target is unsafe")
    if os.name == "posix" and (
        status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise CachePersistenceError("judge cache target must be owner-only")


def _read_private_file(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_FILE_BYTES:
            raise CachePersistenceError("judge cache descriptor is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise CachePersistenceError("judge cache descriptor must be owner-only")
        chunks: list[bytes] = []
        remaining = _MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > _MAX_FILE_BYTES:
            raise CachePersistenceError("judge cache grew beyond its size limit")
        return encoded
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise CachePersistenceError("judge cache write did not make progress")
        written += count


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
