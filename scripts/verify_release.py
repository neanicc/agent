#!/usr/bin/env python3
"""Fail closed unless every release artifact has bound, verified evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:([0-9a-f]{64})$")
ARTIFACT_TYPES = {
    "python-wheel",
    "python-sdist",
    "npm-package",
    "oci-image",
    "web-image",
    "ios-verification-archive",
}


class ReleaseVerificationError(ValueError):
    pass


def verify_release(manifest_path: Path, *, cosign: str | None = None) -> None:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseVerificationError("release manifest must be a regular file")
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    manifest = _object(manifest_path)
    failures: list[str] = []
    if manifest.get("schema_version") != 1:
        failures.append("manifest schema_version must be 1")
    release_commit = manifest.get("release_commit")
    if not isinstance(release_commit, str) or COMMIT.fullmatch(release_commit) is None:
        failures.append("release_commit must be a full lowercase Git commit")
    identity = manifest.get("certificate_identity")
    issuer = manifest.get("certificate_oidc_issuer")
    if not isinstance(identity, str) or not identity or len(identity) > 1_024:
        failures.append("certificate_identity is required and bounded")
    if not isinstance(issuer, str) or not issuer.startswith("https://") or len(issuer) > 1_024:
        failures.append("certificate_oidc_issuer must be HTTPS")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        failures.append("manifest must contain at least one artifact")
        artifacts = []

    names: set[str] = set()
    signer = cosign or shutil.which("cosign")
    for index, value in enumerate(artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(value, dict):
            failures.append(f"{label} must be an object")
            continue
        name = value.get("name")
        if not isinstance(name, str) or not name or len(name) > 256 or name in names:
            failures.append(f"{label}.name must be non-empty and unique")
        else:
            names.add(name)
            label = name
        if value.get("type") not in ARTIFACT_TYPES:
            failures.append(f"{label}: unsupported artifact type")
        if not _bounded(value.get("platform"), 128):
            failures.append(f"{label}: platform is required")
        if value.get("build_commit") != release_commit:
            failures.append(f"{label}: build commit does not match release commit")
        digest = value.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            failures.append(f"{label}: sha256 is missing or invalid")
            continue

        path_value = value.get("path")
        image_value = value.get("image")
        artifact_path: Path | None = None
        if isinstance(path_value, str) == isinstance(image_value, str):
            failures.append(f"{label}: provide exactly one canonical path or image digest")
        elif isinstance(path_value, str):
            try:
                artifact_path = _evidence_path(root, path_value)
                if _sha256(artifact_path) != digest:
                    failures.append(f"{label}: artifact checksum mismatch")
            except ReleaseVerificationError as exc:
                failures.append(f"{label}: {exc}")
        else:
            image_match = IMAGE.fullmatch(str(image_value))
            if image_match is None or image_match.group(1) != digest:
                failures.append(f"{label}: image must be an immutable matching digest")

        evidence_paths: dict[str, Path] = {}
        for kind in ("sbom", "provenance", "signature"):
            try:
                evidence_paths[kind] = _verify_evidence(root, value.get(kind), kind)
            except ReleaseVerificationError as exc:
                failures.append(f"{label}: {exc}")
        if "sbom" in evidence_paths:
            try:
                sbom_text = evidence_paths["sbom"].read_text(encoding="utf-8")
                sbom = json.loads(sbom_text)
                if not isinstance(sbom, dict):
                    raise ReleaseVerificationError("SBOM must contain a JSON object")
                if not (
                    sbom.get("spdxVersion")
                    or sbom.get("bomFormat") == "CycloneDX"
                ):
                    raise ReleaseVerificationError("SBOM is not SPDX or CycloneDX JSON")
                if digest not in sbom_text:
                    raise ReleaseVerificationError(
                        "SBOM is not bound to the artifact digest"
                    )
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ReleaseVerificationError,
            ) as exc:
                failures.append(f"{label}: {exc}")
        if "provenance" in evidence_paths:
            try:
                provenance_text = evidence_paths["provenance"].read_text(
                    encoding="utf-8"
                )
                if digest not in provenance_text:
                    raise ReleaseVerificationError(
                        "provenance is not bound to the artifact digest"
                    )
            except (OSError, UnicodeDecodeError, ReleaseVerificationError) as exc:
                failures.append(f"{label}: {exc}")
        if "signature" in evidence_paths:
            if signer is None:
                failures.append(f"{label}: cosign is required to verify Sigstore evidence")
            elif artifact_path is not None:
                failures.extend(
                    _cosign(
                        signer,
                        [
                            "verify-blob-attestation",
                            "--bundle",
                            str(evidence_paths["signature"]),
                            "--type",
                            "slsaprovenance1",
                            "--certificate-identity",
                            str(identity),
                            "--certificate-oidc-issuer",
                            str(issuer),
                            str(artifact_path),
                        ],
                        label,
                    )
                )
            elif isinstance(image_value, str):
                failures.extend(
                    _cosign(
                        signer,
                        [
                            "verify-attestation",
                            "--bundle",
                            str(evidence_paths["signature"]),
                            "--type",
                            "slsaprovenance1",
                            "--certificate-identity",
                            str(identity),
                            "--certificate-oidc-issuer",
                            str(issuer),
                            image_value,
                        ],
                        label,
                    )
                )

    if failures:
        raise ReleaseVerificationError(
            "release evidence verification failed:\n"
            + "\n".join(f"- {failure}" for failure in failures)
        )


def _verify_evidence(root: Path, value: object, kind: str) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ReleaseVerificationError(f"{kind} path and checksum are required")
    path_value = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path_value, str) or not isinstance(digest, str):
        raise ReleaseVerificationError(f"{kind} path and checksum are invalid")
    path = _evidence_path(root, path_value)
    if SHA256.fullmatch(digest) is None or _sha256(path) != digest:
        raise ReleaseVerificationError(f"{kind} checksum mismatch")
    if path.stat().st_size == 0:
        raise ReleaseVerificationError(f"{kind} must not be empty")
    return path


def _evidence_path(root: Path, value: str) -> Path:
    if not value or "\x00" in value or Path(value).is_absolute():
        raise ReleaseVerificationError("release paths must be non-empty and relative")
    unresolved = root / value
    relative = Path(value)
    cursor = root
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise ReleaseVerificationError("release path must not traverse a symlink")
    candidate = unresolved.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReleaseVerificationError("release path escapes its manifest directory") from exc
    mode = candidate.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ReleaseVerificationError("release path must name a regular non-symlink file")
    return candidate


def _cosign(executable: str, arguments: list[str], label: str) -> list[str]:
    result = subprocess.run(
        [executable, *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode == 0:
        return []
    return [f"{label}: Sigstore verification failed"]


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"{path.name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseVerificationError(f"{path.name} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        verify_release(args.manifest)
    except (OSError, ReleaseVerificationError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Verified release evidence: {args.manifest}")


if __name__ == "__main__":
    main()
