#!/usr/bin/env python3
"""Create digest-bound SPDX/provenance evidence and the release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any


COMMIT = re.compile(r"^[0-9a-f]{40}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
TYPES = {
    "python-wheel",
    "python-sdist",
    "npm-package",
    "oci-image",
    "web-image",
    "ios-verification-archive",
}


def prepare(
    root: Path,
    inventory_path: Path,
    *,
    commit: str,
    builder: str,
) -> None:
    root = root.resolve(strict=True)
    inventory = _inventory(root, inventory_path, commit)
    evidence = root / "evidence"
    evidence.mkdir(mode=0o755, exist_ok=True)
    for artifact in inventory:
        path = root / artifact["path"]
        digest = _sha256(path)
        sbom_path = evidence / f"{artifact['name']}.spdx.json"
        sbom = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": artifact["name"],
            "documentNamespace": (
                f"https://loopguard.dev/sbom/{commit}/{artifact['name']}"
            ),
            "creationInfo": {"creators": ["Tool: LoopGuard release evidence v1"]},
            "packages": [
                {
                    "name": artifact["name"],
                    "SPDXID": "SPDXRef-Package",
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "checksums": [
                        {"algorithm": "SHA256", "checksumValue": digest}
                    ],
                }
            ],
        }
        _write_json(sbom_path, sbom)
        sbom_digest = _sha256(sbom_path)
        provenance = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": artifact["path"], "digest": {"sha256": digest}}
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://loopguard.dev/buildtypes/release/v1",
                    "externalParameters": {
                        "platform": artifact["platform"],
                        "type": artifact["type"],
                    },
                    "resolvedDependencies": [
                        {"uri": builder, "digest": {"gitCommit": commit}},
                        {
                            "uri": sbom_path.relative_to(root).as_posix(),
                            "digest": {"sha256": sbom_digest},
                        },
                    ],
                },
                "runDetails": {"builder": {"id": builder}},
            },
        }
        _write_json(
            evidence / f"{artifact['name']}.provenance.json",
            provenance,
        )


def assemble(
    root: Path,
    inventory_path: Path,
    output: Path,
    *,
    commit: str,
    identity: str,
    issuer: str,
) -> None:
    root = root.resolve(strict=True)
    inventory = _inventory(root, inventory_path, commit)
    artifacts: list[dict[str, Any]] = []
    for value in inventory:
        artifact = root / value["path"]
        evidence = root / "evidence"
        sbom = evidence / f"{value['name']}.spdx.json"
        provenance = evidence / f"{value['name']}.provenance.json"
        signature = evidence / f"{value['name']}.sigstore.json"
        for path in (sbom, provenance, signature):
            _regular_within(root, path)
        artifacts.append(
            {
                **value,
                "sha256": _sha256(artifact),
                "sbom": _descriptor(root, sbom),
                "provenance": _descriptor(root, provenance),
                "signature": _descriptor(root, signature),
            }
        )
    manifest = {
        "schema_version": 1,
        "release_commit": commit,
        "certificate_identity": identity,
        "certificate_oidc_issuer": issuer,
        "artifacts": artifacts,
    }
    output = output.resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("manifest output must remain inside the release root") from exc
    _write_json(output, manifest)


def _inventory(root: Path, path: Path, commit: str) -> list[dict[str, str]]:
    if COMMIT.fullmatch(commit) is None:
        raise ValueError("release commit must be a full lowercase Git commit")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("release inventory schema is invalid")
    values = raw.get("artifacts")
    if not isinstance(values, list) or not values:
        raise ValueError("release inventory must contain artifacts")
    names: set[str] = set()
    result: list[dict[str, str]] = []
    for item in values:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "type", "platform", "path"}
            or NAME.fullmatch(str(item.get("name"))) is None
            or item["name"] in names
            or item.get("type") not in TYPES
            or not isinstance(item.get("platform"), str)
            or not item["platform"]
            or not isinstance(item.get("path"), str)
        ):
            raise ValueError("release inventory artifact is invalid")
        artifact = _regular_within(root, root / item["path"])
        names.add(item["name"])
        result.append(
            {
                "name": item["name"],
                "type": item["type"],
                "platform": item["platform"],
                "path": artifact.relative_to(root).as_posix(),
                "build_commit": commit,
            }
        )
    return result


def _regular_within(root: Path, path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("release inputs must not be symlinks")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("release input escapes the release root") from exc
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("release input must be a regular file")
    return resolved


def _descriptor(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    assemble_parser = commands.add_parser("assemble")
    for subparser in (prepare_parser, assemble_parser):
        subparser.add_argument("--root", type=Path, required=True)
        subparser.add_argument("--inventory", type=Path, required=True)
        subparser.add_argument("--commit", required=True)
    prepare_parser.add_argument("--builder", required=True)
    assemble_parser.add_argument("--identity", required=True)
    assemble_parser.add_argument("--issuer", required=True)
    assemble_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(
            args.root,
            args.inventory,
            commit=args.commit,
            builder=args.builder,
        )
    else:
        assemble(
            args.root,
            args.inventory,
            args.output,
            commit=args.commit,
            identity=args.identity,
            issuer=args.issuer,
        )


if __name__ == "__main__":
    main()
