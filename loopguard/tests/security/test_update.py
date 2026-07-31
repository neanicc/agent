from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from loopguard.security.update import UpdateVerifier


def signed_manifest(
    key: Ed25519PrivateKey,
    **updates: object,
) -> bytes:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "version": "1.2.3",
        "artifact_url": "https://releases.loopguard.dev/loopguard-1.2.3.whl",
        "artifact_digest": "sha256:" + "a" * 64,
        "minimum_database_schema": 3,
        "release_channel": "stable",
    }
    manifest.update(updates)
    canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest["signature"] = base64.urlsafe_b64encode(key.sign(canonical)).decode()
    return json.dumps(manifest).encode()


def test_update_manifest_requires_trusted_signature() -> None:
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    verifier = UpdateVerifier(trusted.public_key())

    assert verifier.verify(signed_manifest(attacker)).code == "invalid_signature"


def test_tampered_signed_manifest_is_rejected() -> None:
    key = Ed25519PrivateKey.generate()
    document = signed_manifest(key).replace(b"1.2.3", b"9.9.9")

    assert UpdateVerifier(key.public_key()).verify(document).code == "invalid_signature"


def test_valid_manifest_and_download_digest_are_verified() -> None:
    key = Ed25519PrivateKey.generate()
    payload = b"release artifact"
    digest = "sha256:133cfccb5b503cf4040c95f3dfad56d07c1574283a1e39066b594f6ee33711ba"
    result = UpdateVerifier(key.public_key()).verify(
        signed_manifest(key, artifact_digest=digest)
    )

    assert result.code == "trusted"
    assert result.manifest is not None
    assert UpdateVerifier.verify_artifact(payload, digest)
    assert not UpdateVerifier.verify_artifact(payload + b"tampered", digest)


def test_update_manifest_rejects_non_https_or_unknown_fields() -> None:
    key = Ed25519PrivateKey.generate()
    verifier = UpdateVerifier(key.public_key())

    assert (
        verifier.verify(
            signed_manifest(key, artifact_url="http://releases.test/update")
        ).code
        == "invalid_manifest"
    )
    assert verifier.verify(signed_manifest(key, execute="curl | sh")).code == "invalid_manifest"
