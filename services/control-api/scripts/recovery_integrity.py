#!/usr/bin/env python3
"""Build and verify the encrypted recovery bundle's integrity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any


ALLOWED_BUNDLE_FILES = frozenset(
    {
        "database.dump",
        "database-state.json",
        "object-manifest.json",
        "recovery-manifest.json",
    }
)
STATE_KEYS = frozenset(
    {
        "max_cloud_ingest_seq",
        "session_sequences",
        "artifact_hashes",
        "action_resolutions",
    }
)


class RecoveryIntegrityError(ValueError):
    pass


def load_state(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    if frozenset(value) != STATE_KEYS:
        raise RecoveryIntegrityError("recovery state has an unexpected schema")
    if not isinstance(value["max_cloud_ingest_seq"], int) or value[
        "max_cloud_ingest_seq"
    ] < 0:
        raise RecoveryIntegrityError("cloud ingest sequence is invalid")
    for key in STATE_KEYS - {"max_cloud_ingest_seq"}:
        if not isinstance(value[key], dict):
            raise RecoveryIntegrityError(f"{key} must be an object")
    if any(
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for digest in value["artifact_hashes"].values()
    ):
        raise RecoveryIntegrityError("artifact hashes are invalid")
    return value


def load_object_manifest(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    objects = value.get("objects")
    if value.get("schema_version") != 1 or not isinstance(objects, list):
        raise RecoveryIntegrityError("object manifest has an unexpected schema")
    seen: set[str] = set()
    for item in objects:
        if not isinstance(item, dict):
            raise RecoveryIntegrityError("object manifest entry must be an object")
        key = item.get("object_key")
        digest = item.get("sha256")
        byte_count = item.get("byte_count")
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 1_024
            or key in seen
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(byte_count, int)
            or byte_count < 0
        ):
            raise RecoveryIntegrityError("object manifest entry is invalid")
        seen.add(key)
    return value


def build_manifest(
    *,
    database_dump: Path,
    database_state: Path,
    object_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    state = load_state(database_state)
    load_object_manifest(object_manifest)
    files = {
        path.name: {
            "sha256": _sha256(path),
            "byte_count": path.stat().st_size,
        }
        for path in (database_dump, database_state, object_manifest)
    }
    manifest = {
        "schema_version": 1,
        "files": files,
        "invariants": state,
    }
    output.write_text(_canonical_json(manifest), encoding="utf-8")
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR)
    return manifest


def verify_bundle(directory: Path) -> dict[str, Any]:
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != ALLOWED_BUNDLE_FILES:
        raise RecoveryIntegrityError("recovery bundle contains unexpected files")
    manifest = _load_object(directory / "recovery-manifest.json")
    if manifest.get("schema_version") != 1 or not isinstance(
        manifest.get("files"), dict
    ):
        raise RecoveryIntegrityError("recovery manifest has an unexpected schema")
    for name in ALLOWED_BUNDLE_FILES - {"recovery-manifest.json"}:
        expected = manifest["files"].get(name)
        path = directory / name
        if (
            not isinstance(expected, dict)
            or expected.get("sha256") != _sha256(path)
            or expected.get("byte_count") != path.stat().st_size
        ):
            raise RecoveryIntegrityError(f"{name} failed integrity verification")
    state = load_state(directory / "database-state.json")
    load_object_manifest(directory / "object-manifest.json")
    if manifest.get("invariants") != state:
        raise RecoveryIntegrityError("recovery invariants do not match the signed bundle")
    return manifest


def extract_bundle(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tarfile.open(archive, mode="r:") as bundle:
        members = bundle.getmembers()
        names = {member.name for member in members}
        if names != ALLOWED_BUNDLE_FILES or any(
            not member.isfile()
            or Path(member.name).is_absolute()
            or len(Path(member.name).parts) != 1
            for member in members
        ):
            raise RecoveryIntegrityError("recovery archive is unsafe")
        for member in members:
            source = bundle.extractfile(member)
            if source is None:
                raise RecoveryIntegrityError("recovery archive member is unreadable")
            target = destination / member.name
            with target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    verify_bundle(destination)


def compare_states(source: Path, restored: Path) -> None:
    if load_state(source) != load_state(restored):
        raise RecoveryIntegrityError("restored database invariants differ from backup")


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryIntegrityError(f"{path.name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RecoveryIntegrityError(f"{path.name} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--database-dump", type=Path, required=True)
    prepare.add_argument("--database-state", type=Path, required=True)
    prepare.add_argument("--object-manifest", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--directory", type=Path, required=True)

    extract = commands.add_parser("extract")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)

    compare = commands.add_parser("compare")
    compare.add_argument("--source", type=Path, required=True)
    compare.add_argument("--restored", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        build_manifest(
            database_dump=args.database_dump,
            database_state=args.database_state,
            object_manifest=args.object_manifest,
            output=args.output,
        )
    elif args.command == "verify":
        verify_bundle(args.directory)
    elif args.command == "extract":
        extract_bundle(args.archive, args.destination)
    else:
        compare_states(args.source, args.restored)


if __name__ == "__main__":
    main()
