import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .manifest import ArtifactManifest
from .models import RunStatus, VerificationRun


_SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_runs (
    run_id TEXT PRIMARY KEY,
    run_json TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    repository_sha TEXT NOT NULL,
    worktree_hash TEXT NOT NULL,
    command_hash TEXT NOT NULL,
    repo_seq INTEGER NOT NULL CHECK(repo_seq >= 0),
    session_seq INTEGER NOT NULL CHECK(session_seq >= 0),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    record_mac TEXT NOT NULL,
    previous_status TEXT,
    recovery_reason TEXT,
    inflight_command_id TEXT,
    inflight_command_hash TEXT
);
CREATE TABLE IF NOT EXISTS artifact_manifests (
    manifest_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    deleted_at TEXT,
    deletion_actor TEXT,
    FOREIGN KEY(run_id) REFERENCES verification_runs(run_id)
);
CREATE INDEX IF NOT EXISTS artifact_manifests_artifact
ON artifact_manifests(artifact_id, deleted_at);
CREATE TABLE IF NOT EXISTS artifact_deletion_audit (
    manifest_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    FOREIGN KEY(manifest_id) REFERENCES artifact_manifests(manifest_id)
);
PRAGMA user_version = 1;
"""


class VerificationStoreError(RuntimeError):
    """Durable verification state could not be read or updated safely."""


class ConcurrentVerificationUpdate(VerificationStoreError):
    """Another process updated the verification run first."""


class VerificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    contract_hash: str
    repository_id: str
    repository_sha: str
    worktree_hash: str
    command_hash: str
    repo_seq: int
    session_seq: int
    revision: int
    previous_status: RunStatus | None = None
    recovery_reason: str | None = None
    inflight_command_id: str | None = None
    inflight_command_hash: str | None = None


class VerificationStore:
    def __init__(self, path: Path, *, integrity_key: bytes) -> None:
        if not integrity_key:
            raise ValueError("verification store integrity key must not be empty")
        self.path = path.expanduser().absolute()
        if self.path.is_symlink():
            raise VerificationStoreError("verification database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._integrity_key = hashlib.sha256(
            b"loopguard-verification-store-integrity-v1\0" + integrity_key
        ).digest()
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            check_same_thread=False,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, 1}:
            self._connection.close()
            raise VerificationStoreError("verification database schema is unsupported")
        owned_objects = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if version == 0:
            if owned_objects:
                self._connection.close()
                raise VerificationStoreError(
                    "unversioned verification database is not empty"
                )
            self._connection.executescript(_SCHEMA)
        else:
            self._validate_schema()
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._validate_schema()
        self._secure_files()
        self._mark_interrupted_runs_recovering()

    def create(
        self,
        run: VerificationRun,
        metadata: VerificationMetadata,
    ) -> None:
        _validate_run_metadata_pair(run, metadata)
        with self._lock:
            self._execute(
                """
                INSERT INTO verification_runs(
                    run_id, run_json, contract_hash, repository_id, repository_sha,
                    worktree_hash, command_hash, repo_seq, session_seq, revision, record_mac,
                    previous_status, recovery_reason, inflight_command_id,
                    inflight_command_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _run_values(run, metadata, self._integrity_key),
            )
            self._secure_files()

    def read(self, run_id: str) -> VerificationRun:
        row = self._row(run_id)
        try:
            return VerificationRun.model_validate_json(row["run_json"])
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored verification run is invalid") from exc

    def metadata(self, run_id: str) -> VerificationMetadata:
        row = self._row(run_id)
        try:
            return VerificationMetadata(
                run_id=row["run_id"],
                contract_hash=row["contract_hash"],
                repository_id=row["repository_id"],
                repository_sha=row["repository_sha"],
                worktree_hash=row["worktree_hash"],
                command_hash=row["command_hash"],
                repo_seq=row["repo_seq"],
                session_seq=row["session_seq"],
                revision=row["revision"],
                previous_status=row["previous_status"],
                recovery_reason=row["recovery_reason"],
                inflight_command_id=row["inflight_command_id"],
                inflight_command_hash=row["inflight_command_hash"],
            )
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored verification metadata is invalid") from exc

    def replace(
        self,
        run: VerificationRun,
        metadata: VerificationMetadata,
    ) -> VerificationMetadata:
        _validate_run_metadata_pair(run, metadata)
        next_revision = metadata.revision + 1
        with self._lock:
            cursor = self._execute(
                """
                UPDATE verification_runs
                SET run_json = ?, contract_hash = ?, repository_id = ?, repository_sha = ?,
                    worktree_hash = ?, command_hash = ?, repo_seq = ?, session_seq = ?,
                    revision = ?, record_mac = ?, previous_status = ?, recovery_reason = ?,
                    inflight_command_id = ?, inflight_command_hash = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    run.model_dump_json(),
                    metadata.contract_hash,
                    metadata.repository_id,
                    metadata.repository_sha,
                    metadata.worktree_hash,
                    metadata.command_hash,
                    metadata.repo_seq,
                    metadata.session_seq,
                    next_revision,
                    _record_mac(
                        run,
                        metadata.model_copy(update={"revision": next_revision}),
                        self._integrity_key,
                    ),
                    metadata.previous_status.value if metadata.previous_status else None,
                    metadata.recovery_reason,
                    metadata.inflight_command_id,
                    metadata.inflight_command_hash,
                    run.run_id,
                    metadata.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrentVerificationUpdate("verification run changed concurrently")
            return metadata.model_copy(update={"revision": next_revision})

    def add_manifest(self, run_id: str, manifest: ArtifactManifest) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT manifest_json, run_id FROM artifact_manifests WHERE manifest_id = ?",
                    (manifest.manifest_id,),
                ).fetchone()
                if existing is not None:
                    if existing["run_id"] != run_id or existing["manifest_json"] != manifest.model_dump_json():
                        raise VerificationStoreError("artifact manifest ID conflicts with stored data")
                else:
                    count = int(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM artifact_manifests WHERE run_id = ?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    if count >= 4096:
                        raise VerificationStoreError(
                            "verification artifact manifest limit exceeded"
                        )
                    self._connection.execute(
                        """
                        INSERT INTO artifact_manifests(
                            manifest_id, artifact_id, run_id, manifest_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            manifest.manifest_id,
                            manifest.artifact_id,
                            run_id,
                            manifest.model_dump_json(),
                        ),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def manifest(self, manifest_id: str) -> tuple[ArtifactManifest, bool]:
        with self._lock:
            row = self._connection.execute(
                "SELECT manifest_json, deleted_at FROM artifact_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"verification artifact manifest {manifest_id!r} does not exist")
        try:
            return ArtifactManifest.model_validate_json(row["manifest_json"]), row["deleted_at"] is not None
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored artifact manifest is invalid") from exc

    def artifact_ids(self, *, include_deleted: bool = True) -> set[str]:
        query = "SELECT DISTINCT artifact_id FROM artifact_manifests"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        with self._lock:
            return {str(row[0]) for row in self._connection.execute(query)}

    def manifests_for_run(
        self,
        run_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[ArtifactManifest]:
        query = "SELECT manifest_json FROM artifact_manifests WHERE run_id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self._lock:
            rows = self._connection.execute(query, (run_id,)).fetchall()
        try:
            manifests = [ArtifactManifest.model_validate_json(row[0]) for row in rows]
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored artifact manifest is invalid") from exc
        if any(manifest.verification_id != run_id for manifest in manifests):
            raise VerificationStoreError("artifact manifest is linked to the wrong run")
        return manifests

    def expired_manifest_ids(self, now: datetime) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT manifest_id, manifest_json FROM artifact_manifests
                WHERE deleted_at IS NULL
                """
            ).fetchall()
        expired: list[str] = []
        try:
            for row in rows:
                manifest = ArtifactManifest.model_validate_json(row["manifest_json"])
                if manifest.expires_at <= now:
                    expired.append(str(row["manifest_id"]))
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored artifact manifest is invalid") from exc
        return sorted(expired)

    def live_manifest_count(self, artifact_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*) FROM artifact_manifests
                WHERE artifact_id = ? AND deleted_at IS NULL
                """,
                (artifact_id,),
            ).fetchone()
        return int(row[0])

    def delete_manifest(self, manifest_id: str, *, actor: str, deleted_at: datetime) -> str:
        if not actor.strip():
            raise ValueError("deletion actor must not be empty")
        encoded_time = deleted_at.isoformat()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT artifact_id, deleted_at FROM artifact_manifests WHERE manifest_id = ?",
                    (manifest_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"artifact manifest {manifest_id!r} does not exist")
                if row["deleted_at"] is None:
                    self._connection.execute(
                        """
                        UPDATE artifact_manifests
                        SET deleted_at = ?, deletion_actor = ?
                        WHERE manifest_id = ?
                        """,
                        (encoded_time, actor.strip(), manifest_id),
                    )
                    self._connection.execute(
                        """
                        INSERT INTO artifact_deletion_audit(
                            manifest_id, artifact_id, actor, deleted_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (manifest_id, row["artifact_id"], actor.strip(), encoded_time),
                    )
                self._connection.execute("COMMIT")
                return str(row["artifact_id"])
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def deletion_audit(self, manifest_id: str) -> dict[str, str]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT artifact_id, actor, deleted_at FROM artifact_deletion_audit
                WHERE manifest_id = ?
                """,
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"artifact manifest {manifest_id!r} has no deletion audit")
        return {name: str(row[name]) for name in ("artifact_id", "actor", "deleted_at")}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _mark_interrupted_runs_recovering(self) -> None:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM verification_runs").fetchall()
            for row in rows:
                run = VerificationRun.model_validate_json(row["run_json"])
                if run.status not in {
                    RunStatus.BASELINING,
                    RunStatus.ACTIVE,
                    RunStatus.COMPLETING,
                }:
                    continue
                metadata = self.metadata(run.run_id)
                recovering = run.model_copy(
                    update={
                        "status": RunStatus.RECOVERING,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self.replace(
                    recovering,
                    metadata.model_copy(
                        update={
                            "previous_status": run.status,
                            "recovery_reason": "daemon_restart",
                        }
                    ),
                )

    def _row(self, run_id: str) -> sqlite3.Row:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM verification_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"verification run {run_id!r} does not exist")
        try:
            run = VerificationRun.model_validate_json(row["run_json"])
            metadata = VerificationMetadata(
                run_id=row["run_id"],
                contract_hash=row["contract_hash"],
                repository_id=row["repository_id"],
                repository_sha=row["repository_sha"],
                worktree_hash=row["worktree_hash"],
                command_hash=row["command_hash"],
                repo_seq=row["repo_seq"],
                session_seq=row["session_seq"],
                revision=row["revision"],
                previous_status=row["previous_status"],
                recovery_reason=row["recovery_reason"],
                inflight_command_id=row["inflight_command_id"],
                inflight_command_hash=row["inflight_command_hash"],
            )
        except (ValidationError, ValueError) as exc:
            raise VerificationStoreError("stored verification record is invalid") from exc
        expected = _record_mac(run, metadata, self._integrity_key)
        if not hmac.compare_digest(str(row["record_mac"]), expected):
            raise VerificationStoreError("stored verification record failed authentication")
        return row

    def _execute(self, sql: str, parameters: tuple[object, ...]) -> sqlite3.Cursor:
        if self._closed:
            raise VerificationStoreError("verification store is closed")
        return self._connection.execute(sql, parameters)

    def _validate_schema(self) -> None:
        expected = {
            "verification_runs": (
                "run_id", "run_json", "contract_hash", "repository_id",
                "repository_sha", "worktree_hash", "command_hash", "repo_seq",
                "session_seq", "revision", "record_mac", "previous_status",
                "recovery_reason", "inflight_command_id", "inflight_command_hash",
            ),
            "artifact_manifests": (
                "manifest_id", "artifact_id", "run_id", "manifest_json",
                "deleted_at", "deletion_actor",
            ),
            "artifact_deletion_audit": (
                "manifest_id", "artifact_id", "actor", "deleted_at",
            ),
        }
        for table, columns in expected.items():
            observed = tuple(
                str(row[1])
                for row in self._connection.execute(f'PRAGMA table_info("{table}")')
            )
            if observed != columns:
                raise VerificationStoreError(
                    f"verification database {table} schema is invalid"
                )
        quick_check = self._connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise VerificationStoreError("verification database integrity check failed")

    def _secure_files(self) -> None:
        for path in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            if path.exists():
                path.chmod(0o600)


def _run_values(
    run: VerificationRun,
    metadata: VerificationMetadata,
    integrity_key: bytes,
) -> tuple[object, ...]:
    return (
        run.run_id,
        run.model_dump_json(),
        metadata.contract_hash,
        metadata.repository_id,
        metadata.repository_sha,
        metadata.worktree_hash,
        metadata.command_hash,
        metadata.repo_seq,
        metadata.session_seq,
        metadata.revision,
        _record_mac(run, metadata, integrity_key),
        metadata.previous_status.value if metadata.previous_status else None,
        metadata.recovery_reason,
        metadata.inflight_command_id,
        metadata.inflight_command_hash,
    )


def _record_mac(
    run: VerificationRun,
    metadata: VerificationMetadata,
    integrity_key: bytes,
) -> str:
    payload = {
        "run": run.model_dump(mode="json"),
        "metadata": metadata.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(
        integrity_key,
        b"loopguard-verification-record-v1\0" + canonical,
        hashlib.sha256,
    ).hexdigest()


def _validate_run_metadata_pair(
    run: VerificationRun,
    metadata: VerificationMetadata,
) -> None:
    if run.run_id != metadata.run_id:
        raise VerificationStoreError("verification run and metadata IDs differ")
    if run.repo_seq != metadata.repo_seq or run.session_seq != metadata.session_seq:
        raise VerificationStoreError("verification run cursors differ from metadata")
    contract_payload = run.contract.model_dump(mode="json")
    expected_contract_hash = hashlib.sha256(
        json.dumps(contract_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    command_payload = {
        "checks": [check.model_dump(mode="json") for check in run.contract.checks]
    }
    expected_command_hash = hashlib.sha256(
        json.dumps(command_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if metadata.contract_hash != expected_contract_hash:
        raise VerificationStoreError("verification contract hash does not match the run")
    if metadata.command_hash != expected_command_hash:
        raise VerificationStoreError("verification command hash does not match the run")
    if run.baseline is not None and metadata.repository_sha != run.baseline.repository_sha:
        raise VerificationStoreError("verification repository SHA differs from the baseline")
