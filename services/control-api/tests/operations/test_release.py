from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
VERIFIER = ROOT / "scripts/verify_release.py"
CREATOR = ROOT / "scripts/create_release_candidate.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate(directory: Path) -> Path:
    files = directory / "files"
    evidence = directory / "evidence"
    files.mkdir(parents=True)
    evidence.mkdir()
    artifact = files / "loopguard.whl"
    artifact.write_bytes(b"wheel candidate")
    artifact_digest = digest(artifact)
    sbom = evidence / "loopguard.spdx.json"
    sbom.write_text(
        json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "name": "loopguard",
                "documentNamespace": f"https://loopguard.dev/sbom/{artifact_digest}",
            }
        )
    )
    provenance = evidence / "loopguard.intoto.jsonl"
    provenance.write_text(
        json.dumps(
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "loopguard.whl", "digest": {"sha256": artifact_digest}}],
            }
        )
    )
    signature = evidence / "loopguard.sigstore.json"
    signature.write_text(json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3"}))
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_commit": "a" * 40,
                "certificate_identity": "https://github.com/neanicc/agent/.github/workflows/release.yml@refs/tags/v1.0.0",
                "certificate_oidc_issuer": "https://token.actions.githubusercontent.com",
                "artifacts": [
                    {
                        "name": "loopguard-wheel",
                        "type": "python-wheel",
                        "platform": "any",
                        "path": "files/loopguard.whl",
                        "sha256": artifact_digest,
                        "build_commit": "a" * 40,
                        "sbom": {"path": "evidence/loopguard.spdx.json", "sha256": digest(sbom)},
                        "provenance": {
                            "path": "evidence/loopguard.intoto.jsonl",
                            "sha256": digest(provenance),
                        },
                        "signature": {
                            "path": "evidence/loopguard.sigstore.json",
                            "sha256": digest(signature),
                        },
                    }
                ],
            }
        )
    )
    return manifest


def fake_cosign(directory: Path, *, success: bool = True) -> Path:
    executable = directory / "cosign"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in verify-attestation|verify-blob-attestation) ;; *) exit 64 ;; esac\n"
        f"exit {0 if success else 1}\n"
    )
    executable.chmod(0o700)
    return executable


def test_verified_candidate_requires_all_bound_evidence(tmp_path: Path) -> None:
    manifest = candidate(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }
    fake_cosign(tmp_path)

    result = subprocess.run(
        ["python3", str(VERIFIER), str(manifest)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Verified release evidence" in result.stdout


def test_unsigned_and_wrong_provenance_candidates_fail_with_complete_reasons(
    tmp_path: Path,
) -> None:
    manifest_path = candidate(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0].pop("signature")
    provenance = tmp_path / "evidence/loopguard.intoto.jsonl"
    provenance.write_text('{"subject":[]}')
    manifest["artifacts"][0]["provenance"]["sha256"] = digest(provenance)
    manifest_path.write_text(json.dumps(manifest))

    result = subprocess.run(
        ["python3", str(VERIFIER), str(manifest_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "signature path and checksum are required" in result.stderr
    assert "provenance is not bound" in result.stderr


def test_manifest_rejects_escape_and_failed_sigstore_verification(tmp_path: Path) -> None:
    manifest_path = candidate(tmp_path)
    outside = tmp_path.parent / "outside-release.whl"
    outside.write_bytes(b"outside")
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0]["path"] = "../outside-release.whl"
    manifest["artifacts"][0]["sha256"] = digest(outside)
    manifest_path.write_text(json.dumps(manifest))
    result = subprocess.run(
        ["python3", str(VERIFIER), str(manifest_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "escapes its manifest directory" in result.stderr

    manifest_path = candidate(tmp_path / "signed")
    fake_cosign(tmp_path, success=False)
    result = subprocess.run(
        ["python3", str(VERIFIER), str(manifest_path)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Sigstore verification failed" in result.stderr


def test_release_shell_wrapper_is_syntax_checked() -> None:
    wrapper = ROOT / "scripts/verify_release.sh"
    subprocess.run(["bash", "-n", str(wrapper)], check=True)
    assert "eval " not in wrapper.read_text()


def test_candidate_creator_binds_sbom_provenance_signature_and_manifest(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    files = release / "files"
    files.mkdir(parents=True)
    artifact = files / "loopguard.whl"
    artifact.write_bytes(b"built-once candidate")
    inventory = release / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "name": "loopguard-wheel",
                        "type": "python-wheel",
                        "platform": "any",
                        "path": "files/loopguard.whl",
                    }
                ],
            }
        )
    )
    commit = "b" * 40
    identity = "https://github.com/neanicc/agent/.github/workflows/release.yml@refs/tags/v1"
    subprocess.run(
        [
            "python3",
            str(CREATOR),
            "prepare",
            "--root",
            str(release),
            "--inventory",
            str(inventory),
            "--commit",
            commit,
            "--builder",
            identity,
        ],
        check=True,
    )
    signature = release / "evidence/loopguard-wheel.sigstore.json"
    signature.write_text(
        json.dumps(
            {
                "mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3",
                "subjectDigest": digest(artifact),
            }
        )
    )
    manifest = release / "manifest.json"
    subprocess.run(
        [
            "python3",
            str(CREATOR),
            "assemble",
            "--root",
            str(release),
            "--inventory",
            str(inventory),
            "--commit",
            commit,
            "--identity",
            identity,
            "--issuer",
            "https://token.actions.githubusercontent.com",
            "--output",
            str(manifest),
        ],
        check=True,
    )
    fake_cosign(tmp_path)
    result = subprocess.run(
        ["python3", str(VERIFIER), str(manifest)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    built = json.loads(manifest.read_text())["artifacts"][0]
    assert built["sha256"] == digest(artifact)
    assert digest(artifact) in (
        release / built["provenance"]["path"]
    ).read_text()
