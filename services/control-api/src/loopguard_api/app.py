from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import re
import time
import uuid
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import AuthService, CachedOidcJwksProvider, DatabaseMembershipStore
from .action_signing import Ed25519ActionSigner
from .actions import ActionService
from .artifacts import ArtifactService, MemoryKms, MemoryObjectStore
from .authorization import Principal, current_principal
from .client_queries import ControlQueryService
from .db import create_database_engine, tenant_session_factory
from .device_pairing import DevicePairingService
from .errors import ApiProblem, problem_response
from .hook_ingest import HookService
from .pairing import PairingService
from .routes.hooks import router as hooks_router
from .routes.hosts import router as hosts_router
from .routes.actions import router as actions_router
from .routes.artifacts import router as artifacts_router
from .routes.audit import router as audit_router
from .routes.capabilities import router as capabilities_router
from .routes.changes import router as changes_router
from .routes.costs import router as costs_router
from .routes.devices import router as devices_router
from .routes.sessions import router as sessions_router
from .routes.preferences import router as preferences_router
from .routes.repairs import router as repairs_router
from .routes.verifications import router as verifications_router
from .settings import PublicBuild, Settings
from .stream_tickets import StreamTicketService
from .subscriptions import SessionSubscriptionService


_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FORWARDED_HEADERS = {b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"}
logger = logging.getLogger("loopguard_api.requests")


class ValidationFixture(BaseModel):
    count: int = Field(gt=0)


def create_app(
    settings: Settings | None = None,
    *,
    auth_service: AuthService | None = None,
    stream_ticket_service: StreamTicketService | None = None,
    subscription_service: SessionSubscriptionService | None = None,
    action_service: ActionService | None = None,
    artifact_service: ArtifactService | None = None,
    control_queries: ControlQueryService | None = None,
    device_pairing_service: DevicePairingService | None = None,
) -> FastAPI:
    active = settings or Settings()
    app = FastAPI(title="LoopGuard Control API", version="0.1.0")
    app.state.settings = active
    if auth_service is None:
        engine = create_database_engine(active.database_url)
        app.state.database_engine = engine
        auth_service = AuthService(
            issuer=active.oidc_issuer,
            audience=active.oidc_audience,
            jwks=CachedOidcJwksProvider(
                active.oidc_issuer,
                ttl_seconds=active.oidc_jwks_cache_ttl_seconds,
                timeout_seconds=active.oidc_http_timeout_seconds,
            ),
            memberships=DatabaseMembershipStore(tenant_session_factory(engine)),
            allowed_algorithms=active.oidc_allowed_algorithms,
        )
    app.state.auth_service = auth_service
    app.state.pairing_service = PairingService()
    app.state.hook_service = HookService(maximum_body_bytes=active.max_request_bytes)
    app.state.stream_ticket_service = stream_ticket_service or StreamTicketService()
    app.state.subscription_service = subscription_service or SessionSubscriptionService()
    if action_service is None:
        if active.environment in {"staging", "production"}:
            raise ValueError("hosted deployments require an external action signer adapter")
        action_service = ActionService(
            signer=Ed25519ActionSigner.generate(active.action_signing_active_key_id)
        )
    app.state.action_service = action_service
    if artifact_service is None:
        if active.environment in {"staging", "production"}:
            raise ValueError("hosted deployments require object-store and KMS adapters")
        artifact_service = ArtifactService(objects=MemoryObjectStore(), kms=MemoryKms())
    app.state.artifact_service = artifact_service
    if control_queries is None:
        if active.environment in {"staging", "production"}:
            raise ValueError("hosted deployments require a durable control-query adapter")
        control_queries = ControlQueryService()
    if device_pairing_service is None:
        if active.environment in {"staging", "production"}:
            raise ValueError("hosted deployments require a durable device-pairing adapter")
        device_pairing_service = DevicePairingService()
    app.state.control_queries = control_queries
    app.state.device_pairing_service = device_pairing_service

    @app.exception_handler(ApiProblem)
    async def api_problem(request: Request, exc: ApiProblem):
        return problem_response(
            exc.code,
            _request_id(request.scope),
            field=exc.field,
            current_state=exc.current_state,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        field = ".".join(str(part) for part in errors[0].get("loc", ())) if errors else None
        return problem_response("LGAPI-REQUEST-INVALID", _request_id(request.scope), field=field)

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, exc: StarletteHTTPException):
        code = {
            401: "LGAPI-UNAUTHORIZED",
            403: "LGAPI-FORBIDDEN",
            404: "LGAPI-NOT-FOUND",
            405: "LGAPI-METHOD-NOT-ALLOWED",
        }.get(exc.status_code, "LGAPI-INTERNAL")
        return problem_response(code, _request_id(request.scope))

    @app.exception_handler(Exception)
    async def internal_problem(request: Request, exc: Exception):
        logger.error(
            "unhandled request failure",
            extra={
                "request_id": _request_id(request.scope),
                "error_type": type(exc).__name__,
            },
        )
        return problem_response("LGAPI-INTERNAL", _request_id(request.scope))

    @app.get("/health", response_model=PublicBuild)
    async def health() -> PublicBuild:
        return PublicBuild(build_sha=active.build_sha, environment=active.environment)

    @app.get("/v1/me")
    async def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        return {
            "tenant_id": str(principal.tenant_id),
            "role": principal.role,
            "permissions": sorted(principal.permissions),
        }

    if active.environment == "test":
        @app.post("/_test/validate", include_in_schema=False)
        async def validate_fixture(body: ValidationFixture) -> dict[str, int]:
            return {"count": body.count}

        @app.get("/_test/fail", include_in_schema=False)
        async def fail_fixture() -> None:
            raise RuntimeError("database-password must never escape")

    app.include_router(hosts_router)
    app.include_router(hooks_router)
    app.include_router(sessions_router)
    app.include_router(actions_router)
    app.include_router(artifacts_router)
    app.include_router(audit_router)
    app.include_router(capabilities_router)
    app.include_router(changes_router)
    app.include_router(costs_router)
    app.include_router(devices_router)
    app.include_router(preferences_router)
    app.include_router(repairs_router)
    app.include_router(verifications_router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-CSRF-Token", "X-Request-ID"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(active.trusted_hosts))
    app.add_middleware(SecurityBoundaryMiddleware, settings=active)
    return app


class SecurityBoundaryMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.proxy_networks = tuple(
            ipaddress.ip_network(value) for value in settings.trusted_proxy_cidrs
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided_id = headers.get(b"x-request-id", b"").decode("ascii", "ignore")
        request_id = provided_id if _REQUEST_ID.fullmatch(provided_id) else f"req_{uuid.uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status_code = 500

        async def boundary_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                if not any(key.lower() == b"x-request-id" for key, _ in response_headers):
                    response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        async def reject(code: str) -> None:
            await problem_response(code, request_id)(scope, receive, boundary_send)

        if self._untrusted_forwarding(scope, headers):
            await reject("LGAPI-PROXY-UNTRUSTED")
            return
        if self._untrusted_host(headers):
            await reject("LGAPI-HOST-UNTRUSTED")
            return
        origin = headers.get(b"origin", b"").decode("ascii", "ignore")
        if origin and origin not in self.settings.allowed_origins:
            await reject("LGAPI-ORIGIN-DENIED")
            return
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > self.settings.max_request_bytes
            except ValueError:
                too_large = True
            if too_large:
                await reject("LGAPI-BODY-TOO-LARGE")
                return
        if self._csrf_invalid(scope, headers):
            await reject("LGAPI-CSRF-REQUIRED")
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.settings.max_request_bytes:
                    raise _BodyTooLarge
            return message

        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await self.app(scope, limited_receive, boundary_send)
        except _BodyTooLarge:
            await reject("LGAPI-BODY-TOO-LARGE")
        except TimeoutError:
            await reject("LGAPI-TIMEOUT")
        finally:
            logger.info(
                "request complete",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": round((time.monotonic() - started) * 1_000, 3),
                },
            )

    def _untrusted_forwarding(self, scope: Scope, headers: dict[bytes, bytes]) -> bool:
        if not _FORWARDED_HEADERS.intersection(headers):
            return False
        client = scope.get("client")
        if not client:
            return True
        try:
            address = ipaddress.ip_address(client[0])
        except ValueError:
            return True
        return not any(address in network for network in self.proxy_networks)

    def _csrf_invalid(self, scope: Scope, headers: dict[bytes, bytes]) -> bool:
        if scope.get("method") in {"GET", "HEAD", "OPTIONS"} or b"cookie" not in headers:
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(headers[b"cookie"].decode("latin-1"))
        except Exception:
            return True
        if "session" not in cookies:
            return False
        origin = headers.get(b"origin", b"").decode("ascii", "ignore")
        supplied = headers.get(b"x-csrf-token", b"").decode("ascii", "ignore")
        expected = cookies.get("loopguard_csrf")
        return (
            origin not in self.settings.allowed_origins
            or expected is None
            or not supplied
            or not hmac.compare_digest(supplied, expected.value)
        )

    def _untrusted_host(self, headers: dict[bytes, bytes]) -> bool:
        raw = headers.get(b"host", b"").decode("ascii", "ignore")
        try:
            hostname = urlsplit(f"//{raw}").hostname
        except ValueError:
            return True
        return hostname is None or hostname.lower() not in self.settings.trusted_hosts


class _BodyTooLarge(Exception):
    pass


def _request_id(scope: Scope) -> str:
    value: Any = scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else "req_unknown"
