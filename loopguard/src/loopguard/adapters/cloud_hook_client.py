from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from loopguard.control.decisions import PolicyDecision
from loopguard.control.events import ControlEvent

from .hook_client import HookClient, HookClientError


MAX_CLOUD_RESPONSE_BYTES = 65_536
DEFAULT_CLOUD_TIMEOUT_SECONDS = 0.5


class HookDecisionClient(Protocol):
    def send(self, event: ControlEvent) -> PolicyDecision: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(slots=True)
class CloudHookClient:
    url: str
    key_id: str
    secret_b64: str
    timeout_seconds: float = DEFAULT_CLOUD_TIMEOUT_SECONDS
    opener: Any = None
    clock: Callable[[], float] = time.time
    nonce_factory: Callable[[], str] = lambda: uuid.uuid4().hex
    _secret: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("LoopGuard cloud ingest URL must use HTTPS without URL credentials")
        if parsed.query or parsed.fragment or parsed.path != "/v1/hook-events":
            raise ValueError("LoopGuard cloud ingest URL must target exactly /v1/hook-events")
        if not self.key_id.strip() or any(char.isspace() for char in self.key_id):
            raise ValueError("LoopGuard hook key ID must be a non-empty token")
        if not 0 < self.timeout_seconds <= DEFAULT_CLOUD_TIMEOUT_SECONDS:
            raise ValueError("LoopGuard cloud hook timeout exceeds the 500 ms safety budget")
        try:
            secret = base64.b64decode(self.secret_b64, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("LoopGuard hook secret must be valid URL-safe base64") from exc
        if len(secret) < 32:
            raise ValueError("LoopGuard hook secret must contain at least 256 bits")
        self._secret = secret
        if self.opener is None:
            self.opener = build_opener(_NoRedirect())

    @classmethod
    def from_environment(cls) -> CloudHookClient | None:
        values = (
            os.environ.get("LOOPGUARD_CLOUD_INGEST_URL"),
            os.environ.get("LOOPGUARD_HOOK_KEY_ID"),
            os.environ.get("LOOPGUARD_HOOK_SECRET"),
        )
        if not all(values):
            return None
        return cls(url=str(values[0]), key_id=str(values[1]), secret_b64=str(values[2]))

    def send(self, event: ControlEvent) -> PolicyDecision:
        body = json.dumps(
            event.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(self.clock()))
        nonce = self.nonce_factory()
        body_hash = hashlib.sha256(body).hexdigest()
        canonical = f"POST\n/v1/hook-events\n{timestamp}\n{nonce}\n{body_hash}"
        signature = hmac.new(self._secret, canonical.encode(), hashlib.sha256).hexdigest()
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-LoopGuard-Key-Id": self.key_id,
                "X-LoopGuard-Timestamp": timestamp,
                "X-LoopGuard-Nonce": nonce,
                "X-LoopGuard-Content-SHA256": body_hash,
                "X-LoopGuard-Signature": f"v1={signature}",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise HookClientError("cloud_response_rejected")
                raw = response.read(MAX_CLOUD_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = "cloud_redirect_refused" if 300 <= exc.code < 400 else "cloud_response_rejected"
            raise HookClientError(code) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise HookClientError("cloud_unreachable") from exc
        if len(raw) > MAX_CLOUD_RESPONSE_BYTES:
            raise HookClientError("cloud_response_too_large")
        try:
            return PolicyDecision.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise HookClientError("cloud_invalid_decision") from exc


@dataclass(slots=True)
class FallbackHookClient:
    local: HookDecisionClient
    cloud: HookDecisionClient | None = None

    @classmethod
    def from_environment(cls) -> FallbackHookClient:
        try:
            cloud = CloudHookClient.from_environment()
        except ValueError:
            cloud = None
        return cls(local=HookClient(), cloud=cloud)

    def send(self, event: ControlEvent) -> PolicyDecision:
        try:
            return self.local.send(event)
        except HookClientError:
            if self.cloud is None:
                raise
            return self.cloud.send(event)
