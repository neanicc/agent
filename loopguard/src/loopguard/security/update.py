from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class UpdateVerification:
    code: str
    manifest: Mapping[str, Any] | None = None

    @property
    def trusted(self) -> bool:
        return self.code == "trusted"


class UpdateVerifier:
    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self.public_key = public_key

    def verify(self, document: bytes) -> UpdateVerification:
        try:
            parsed = json.loads(document)
            if not isinstance(parsed, dict):
                return UpdateVerification("invalid_manifest")
            signature_value = parsed.pop("signature")
            signature = base64.b64decode(
                signature_value,
                altchars=b"-_",
                validate=True,
            )
            if not _valid_manifest(parsed):
                return UpdateVerification("invalid_manifest")
            canonical = json.dumps(
                parsed,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.public_key.verify(signature, canonical)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return UpdateVerification("invalid_manifest")
        except InvalidSignature:
            return UpdateVerification("invalid_signature")
        return UpdateVerification("trusted", parsed)

    @staticmethod
    def verify_artifact(payload: bytes, expected_digest: str) -> bool:
        return "sha256:" + hashlib.sha256(payload).hexdigest() == expected_digest


def _valid_manifest(value: Mapping[str, Any]) -> bool:
    if set(value) != {
        "schema_version",
        "version",
        "artifact_url",
        "artifact_digest",
        "minimum_database_schema",
        "release_channel",
    }:
        return False
    url = urlsplit(str(value["artifact_url"]))
    return (
        value["schema_version"] == 1
        and isinstance(value["version"], str)
        and _SEMVER.fullmatch(value["version"]) is not None
        and url.scheme == "https"
        and bool(url.hostname)
        and url.username is None
        and url.password is None
        and isinstance(value["artifact_digest"], str)
        and _DIGEST.fullmatch(value["artifact_digest"]) is not None
        and isinstance(value["minimum_database_schema"], int)
        and 0 <= value["minimum_database_schema"] <= 2_147_483_647
        and value["release_channel"] in {"stable", "beta", "nightly"}
    )
