from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from loopguard.control.paths import ensure_private_home

from .compiler import PreferenceCompiler
from .learning import PreferenceLearner
from .models import Identifier, NonEmptyText, PreferenceProfile, PreferenceVerdict
from .store import PreferenceActor


_SCHEMA_VERSION = 1
_IDENTIFIER = TypeAdapter(Identifier)
_SCHEMA = """
CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    manifest_hash TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE profile_cache (
    scope_key TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
    generation INTEGER NOT NULL CHECK(generation >= 1)
);
CREATE TABLE verdicts (
    verdict_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
    verdict_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE overrides (
    override_id TEXT PRIMARY KEY,
    verdict_id TEXT NOT NULL REFERENCES verdicts(verdict_id),
    actor_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('run', 'repository')),
    scope_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX overrides_verdict ON overrides(verdict_id, scope, scope_id);
PRAGMA user_version = 1;
"""
_EXPECTED_COLUMNS = {
    "profiles": {"profile_id", "manifest_hash", "profile_json", "created_at"},
    "profile_cache": {"scope_key", "cache_key", "profile_id", "generation"},
    "verdicts": {"verdict_id", "profile_id", "verdict_json", "created_at"},
    "overrides": {
        "override_id", "verdict_id", "actor_json", "reason", "scope", "scope_id", "created_at"
    },
}


class PreferenceServiceError(ValueError):
    """Durable preference policy state is invalid or cannot be trusted."""


class PreferenceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    override_id: Identifier
    verdict_id: Identifier
    actor: PreferenceActor
    reason: NonEmptyText = Field(max_length=2_048)
    scope: Literal["run", "repository"]
    scope_id: NonEmptyText = Field(max_length=512)
    created_at: AwareDatetime


class PreferenceService:
    def __init__(
        self,
        path: str | Path,
        *,
        compiler: PreferenceCompiler | None = None,
        learner: PreferenceLearner | None = None,
    ) -> None:
        self.path = Path(path).expanduser().absolute()
        ensure_private_home(self.path.parent)
        identity = self._prepare_path()
        self.compiler = compiler or PreferenceCompiler()
        self.learner = learner
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(
                self.path, isolation_level=None, check_same_thread=False, timeout=5
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._connection.execute("PRAGMA synchronous=FULL")
            opened = os.lstat(self.path)
            if (opened.st_dev, opened.st_ino) != identity:
                raise PreferenceServiceError("preference policy database changed while opening")
            self._initialize_schema()
        except Exception:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise

    @classmethod
    def for_path(
        cls,
        path: str | Path,
        *,
        compiler: PreferenceCompiler | None = None,
        learner: PreferenceLearner | None = None,
    ) -> PreferenceService:
        return cls(path, compiler=compiler, learner=learner)

    def compile_profile(
        self,
        *,
        repo: str | Path,
        cache_scope: str,
        user_profile: Mapping[str, Any] | None = None,
        organization_policy: Mapping[str, Any] | None = None,
    ) -> PreferenceProfile:
        learned = self.learner.derive_rules() if self.learner is not None else []
        promoted = [rule for rule in learned if rule.source.value == "explicit"]
        soft = [rule for rule in learned if rule.source.value == "learned"]
        effective_user = dict(user_profile or {})
        if promoted:
            existing = effective_user.get("rules", [])
            if not isinstance(existing, list):
                raise PreferenceServiceError("user profile rules must be a list")
            effective_user["rules"] = [
                *existing,
                *[rule.model_dump(mode="json") for rule in promoted],
            ]
        profile = self.compiler.compile(
            repo=repo,
            user_profile=effective_user,
            organization_policy=organization_policy,
            learned_rules=soft,
        )
        cache_key = _hash_json(
            {
                "compiler": f"{type(self.compiler).__module__}.{type(self.compiler).__qualname__}:1",
                "profile": profile.model_dump(mode="json"),
            }
        )
        self.register_profile(profile, cache_scope=cache_scope, cache_key=cache_key)
        return profile

    def register_profile(
        self,
        profile: PreferenceProfile,
        *,
        cache_scope: str,
        cache_key: str | None = None,
    ) -> PreferenceProfile:
        profile = PreferenceProfile.model_validate(profile)
        scope = _bounded(cache_scope, "cache scope", 512)
        profile_json = profile.model_dump_json()
        manifest_hash = _hash_json(
            [item.model_dump(mode="json") for item in profile.source_manifest]
        )
        effective_cache_key = cache_key or _hash_json(profile.model_dump(mode="json"))
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction():
            row = self._connection.execute(
                "SELECT profile_json FROM profiles WHERE profile_id=?", (profile.profile_id,)
            ).fetchone()
            if row is not None and row["profile_json"] != profile_json:
                raise PreferenceServiceError("profile ID has conflicting semantics")
            self._connection.execute(
                "INSERT OR IGNORE INTO profiles VALUES (?,?,?,?)",
                (profile.profile_id, manifest_hash, profile_json, now),
            )
            cached = self._connection.execute(
                "SELECT generation FROM profile_cache WHERE scope_key=?", (scope,)
            ).fetchone()
            generation = 1 if cached is None else int(cached["generation"]) + 1
            self._connection.execute(
                "INSERT INTO profile_cache VALUES (?,?,?,?) "
                "ON CONFLICT(scope_key) DO UPDATE SET cache_key=excluded.cache_key, "
                "profile_id=excluded.profile_id, generation=excluded.generation",
                (scope, effective_cache_key, profile.profile_id, generation),
            )
        return profile

    def invalidate(self, cache_scope: str) -> None:
        scope = _bounded(cache_scope, "cache scope", 512)
        with self._transaction():
            self._connection.execute("DELETE FROM profile_cache WHERE scope_key=?", (scope,))

    def cached_profile_id(self, cache_scope: str) -> str | None:
        scope = _bounded(cache_scope, "cache scope", 512)
        with self._lock:
            row = self._connection.execute(
                "SELECT profile_id FROM profile_cache WHERE scope_key=?", (scope,)
            ).fetchone()
        return None if row is None else str(row["profile_id"])

    def profile(self, profile_id: str) -> PreferenceProfile:
        normalized = _identifier(profile_id, "profile ID")
        with self._lock:
            row = self._connection.execute(
                "SELECT profile_json FROM profiles WHERE profile_id=?", (normalized,)
            ).fetchone()
        if row is None:
            raise PreferenceServiceError("preference profile does not exist")
        try:
            return PreferenceProfile.model_validate_json(row["profile_json"])
        except ValidationError as exc:
            raise PreferenceServiceError("stored preference profile is invalid") from exc

    def record_verdict(self, profile_id: str, verdict: PreferenceVerdict) -> PreferenceVerdict:
        profile = self.profile(profile_id)
        verdict = PreferenceVerdict.model_validate(verdict)
        rule = profile.rule(verdict.rule_id)
        if rule is None:
            raise PreferenceServiceError("preference verdict rule is outside the profile")
        if verdict.severity is not rule.severity:
            raise PreferenceServiceError("preference verdict severity differs from its profile")
        verdict_json = verdict.model_dump_json()
        with self._transaction():
            row = self._connection.execute(
                "SELECT profile_id,verdict_json FROM verdicts WHERE verdict_id=?",
                (verdict.verdict_id,),
            ).fetchone()
            if row is not None:
                if row["profile_id"] != profile.profile_id or row["verdict_json"] != verdict_json:
                    raise PreferenceServiceError("verdict ID has conflicting semantics")
                return verdict
            self._connection.execute(
                "INSERT INTO verdicts VALUES (?,?,?,?)",
                (
                    verdict.verdict_id,
                    profile.profile_id,
                    verdict_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return verdict

    def verdict(self, verdict_id: str) -> PreferenceVerdict:
        normalized = _identifier(verdict_id, "verdict ID")
        with self._lock:
            row = self._connection.execute(
                "SELECT verdict_json FROM verdicts WHERE verdict_id=?", (normalized,)
            ).fetchone()
        if row is None:
            raise PreferenceServiceError("preference verdict does not exist")
        try:
            return PreferenceVerdict.model_validate_json(row["verdict_json"])
        except ValidationError as exc:
            raise PreferenceServiceError("stored preference verdict is invalid") from exc

    def override(
        self,
        verdict_id: str,
        *,
        actor: PreferenceActor,
        reason: str,
        scope: Literal["run", "repository"],
        scope_id: str,
    ) -> PreferenceOverride:
        verdict = self.verdict(verdict_id)
        actor = PreferenceActor.model_validate(actor)
        normalized_reason = _bounded(reason, "override reason", 2_048)
        normalized_scope_id = _bounded(scope_id, "override scope ID", 512)
        payload = {
            "verdict_id": verdict.verdict_id,
            "actor": actor.model_dump(mode="json"),
            "reason": normalized_reason,
            "scope": scope,
            "scope_id": normalized_scope_id,
        }
        override_id = f"override-{_hash_json(payload)[:24]}"
        now = datetime.now(timezone.utc)
        override = PreferenceOverride(
            override_id=override_id,
            verdict_id=verdict.verdict_id,
            actor=actor,
            reason=normalized_reason,
            scope=scope,
            scope_id=normalized_scope_id,
            created_at=now,
        )
        with self._transaction():
            existing = self._connection.execute(
                "SELECT actor_json,reason,scope,scope_id FROM overrides WHERE override_id=?",
                (override_id,),
            ).fetchone()
            if existing is not None:
                return self._override(override_id)
            self._connection.execute(
                "INSERT INTO overrides VALUES (?,?,?,?,?,?,?)",
                (
                    override.override_id,
                    override.verdict_id,
                    actor.model_dump_json(),
                    override.reason,
                    override.scope,
                    override.scope_id,
                    now.isoformat(),
                ),
            )
        return override

    def effective_verdicts(
        self,
        verdict_ids: Sequence[str],
        *,
        run_id: str,
        repository_id: str,
    ) -> list[tuple[PreferenceVerdict, PreferenceOverride | None]]:
        if len(verdict_ids) > 1_024:
            raise PreferenceServiceError("preference verdict limit exceeded")
        result: list[tuple[PreferenceVerdict, PreferenceOverride | None]] = []
        for verdict_id in verdict_ids:
            verdict = self.verdict(verdict_id)
            with self._lock:
                row = self._connection.execute(
                    "SELECT override_id FROM overrides WHERE verdict_id=? AND "
                    "((scope='run' AND scope_id=?) OR (scope='repository' AND scope_id=?)) "
                    "ORDER BY CASE scope WHEN 'run' THEN 0 ELSE 1 END, created_at, override_id LIMIT 1",
                    (verdict.verdict_id, run_id, repository_id),
                ).fetchone()
            result.append((verdict, None if row is None else self._override(row["override_id"])))
        return result

    def digest_warnings(self, verdict_ids: Sequence[str]) -> dict[str, str]:
        warnings: dict[str, str] = {}
        for verdict_id in verdict_ids[:1_024]:
            verdict = self.verdict(verdict_id)
            if verdict.status.value == "violation" and verdict.severity.value != "block":
                warnings[verdict.verdict_id] = verdict.summary
        return warnings

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def _override(self, override_id: str) -> PreferenceOverride:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM overrides WHERE override_id=?", (override_id,)
            ).fetchone()
        if row is None:
            raise PreferenceServiceError("preference override does not exist")
        return PreferenceOverride(
            override_id=row["override_id"],
            verdict_id=row["verdict_id"],
            actor=PreferenceActor.model_validate_json(row["actor_json"]),
            reason=row["reason"],
            scope=row["scope"],
            scope_id=row["scope_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _prepare_path(self) -> tuple[int, int]:
        if self.path.is_symlink():
            raise PreferenceServiceError("preference policy database cannot be a symlink")
        try:
            status = os.lstat(self.path)
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            os.close(descriptor)
            status = os.lstat(self.path)
        if not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
            raise PreferenceServiceError("preference policy database path is unsafe")
        if os.name == "posix" and (
            status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600
        ):
            raise PreferenceServiceError("preference policy database must be owner-only")
        return status.st_dev, status.st_ino

    def _initialize_schema(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if version == 0 and not tables:
            self._connection.executescript(_SCHEMA)
            return
        if version != _SCHEMA_VERSION or tables != set(_EXPECTED_COLUMNS):
            raise PreferenceServiceError("preference policy database schema is incompatible")
        for table, expected in _EXPECTED_COLUMNS.items():
            columns = {
                str(row[1])
                for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if columns != expected:
                raise PreferenceServiceError("preference policy database schema is incompatible")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()


def _identifier(value: str, label: str) -> str:
    try:
        return _IDENTIFIER.validate_python(value)
    except ValidationError as exc:
        raise PreferenceServiceError(f"{label} is invalid") from exc


def _bounded(value: str, label: str, maximum: int) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise PreferenceServiceError(f"{label} must be bounded and non-empty")
    return normalized


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
