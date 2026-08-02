from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.recovery_integrity import (
    ALLOWED_BUNDLE_FILES,
    RecoveryIntegrityError,
    build_manifest,
    compare_states,
    extract_bundle,
    verify_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def source_files(directory: Path) -> tuple[Path, Path, Path, Path]:
    database_dump = directory / "database.dump"
    database_dump.write_bytes(b"PGDMP production-like fixture")
    database_state = directory / "database-state.json"
    database_state.write_text(
        json.dumps(
            {
                "max_cloud_ingest_seq": 91,
                "session_sequences": {"session-a": 19, "session-b": 72},
                "artifact_hashes": {"artifact-a": "a" * 64},
                "action_resolutions": {"action-a": {"state": "executed"}},
            }
        )
    )
    object_manifest = directory / "object-manifest.json"
    object_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "objects": [
                    {
                        "object_key": "tenants/t1/artifacts/a1",
                        "sha256": "a" * 64,
                        "byte_count": 42,
                    }
                ],
            }
        )
    )
    recovery_manifest = directory / "recovery-manifest.json"
    build_manifest(
        database_dump=database_dump,
        database_state=database_state,
        object_manifest=object_manifest,
        output=recovery_manifest,
    )
    return database_dump, database_state, object_manifest, recovery_manifest


def test_restore_preserves_stream_positions_and_artifact_hash(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source_files(bundle)
    manifest = verify_bundle(bundle)
    restored_state = tmp_path / "restored-state.json"
    restored_state.write_text((bundle / "database-state.json").read_text())

    compare_states(bundle / "database-state.json", restored_state)

    restored = json.loads(restored_state.read_text())
    source = manifest["invariants"]
    assert restored["max_cloud_ingest_seq"] == source["max_cloud_ingest_seq"]
    assert restored["session_sequences"] == source["session_sequences"]
    assert restored["artifact_hashes"] == source["artifact_hashes"]
    assert restored["action_resolutions"] == source["action_resolutions"]


def test_restore_rejects_tampered_dump_and_state(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source_files(bundle)
    (bundle / "database.dump").write_bytes(b"tampered")
    with pytest.raises(RecoveryIntegrityError, match="database.dump"):
        verify_bundle(bundle)

    bundle = tmp_path / "second"
    bundle.mkdir()
    source_files(bundle)
    restored = tmp_path / "wrong-state.json"
    state = json.loads((bundle / "database-state.json").read_text())
    state["max_cloud_ingest_seq"] += 1
    restored.write_text(json.dumps(state))
    with pytest.raises(RecoveryIntegrityError, match="differ"):
        compare_states(bundle / "database-state.json", restored)


def test_archive_extraction_rejects_traversal_and_links(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar"
    with tarfile.open(archive, "w") as output:
        for name in ALLOWED_BUNDLE_FILES - {"database.dump"}:
            payload = b"{}"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
        info = tarfile.TarInfo("../database.dump")
        info.size = 4
        output.addfile(info, io.BytesIO(b"evil"))

    with pytest.raises(RecoveryIntegrityError, match="unsafe"):
        extract_bundle(archive, tmp_path / "output")


def test_migration_rehearsal_runs_old_and_new_probes_without_eval(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    alembic = bin_dir / "alembic"
    alembic.write_text("#!/bin/sh\nprintf 'alembic %s\\n' \"$*\" >>\"$CALL_LOG\"\n")
    old_probe = tmp_path / "old-probe"
    old_probe.write_text("#!/bin/sh\nprintf 'old\\n' >>\"$CALL_LOG\"\n")
    new_probe = tmp_path / "new-probe"
    new_probe.write_text("#!/bin/sh\nprintf 'new\\n' >>\"$CALL_LOG\"\n")
    for executable in (alembic, old_probe, new_probe):
        executable.chmod(0o700)

    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "LOOPGUARD_REHEARSAL_DATABASE_URL": "postgresql://isolated/rehearsal",
        "LOOPGUARD_OLD_APP_PROBE": str(old_probe),
        "LOOPGUARD_NEW_APP_PROBE": str(new_probe),
        "LOOPGUARD_MIGRATION_MAX_TRANSITION_MS": "30000",
    }
    subprocess.run(
        ["bash", str(SCRIPTS / "rehearse_migration.sh")],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert log.read_text().splitlines() == [
        "alembic upgrade 0002",
        "old",
        "alembic upgrade 0003",
        "old",
        "new",
        "alembic upgrade head",
        "new",
    ]


@pytest.mark.parametrize("name", ["backup.sh", "restore.sh", "rehearse_migration.sh"])
def test_operation_scripts_have_safe_shell_syntax_and_help(name: str) -> None:
    script = SCRIPTS / name
    subprocess.run(["bash", "-n", str(script)], check=True)
    result = subprocess.run(
        ["bash", str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Usage:" in result.stdout
    assert "eval " not in script.read_text()
