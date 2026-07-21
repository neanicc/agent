from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import select

from .authorization import Principal, ROLE_PERMISSIONS
from .db import TenantContext, TenantSessionFactory, tenant_transaction
from .models import Membership, User


class JwksProvider(Protocol):
    async def get_jwk(self, key_id: str) -> Mapping[str, Any] | None: ...


class MembershipStore(Protocol):
    async def find(self, tenant_id: uuid.UUID, subject: str) -> Principal | None: ...


class StaticJwksProvider:
    def __init__(self, jwks: Mapping[str, Any]) -> None:
        self._keys = _index_jwks(jwks)

    async def get_jwk(self, key_id: str) -> Mapping[str, Any] | None:
        return self._keys.get(key_id)


class CachedOidcJwksProvider:
    """Bounded OIDC discovery/JWKS cache with refresh on an unknown key ID."""

    def __init__(
        self,
        issuer: str,
        *,
        ttl_seconds: int = 300,
        timeout_seconds: float = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/") + "/"
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._keys: dict[str, Mapping[str, Any]] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_jwk(self, key_id: str) -> Mapping[str, Any] | None:
        if not key_id or len(key_id) > 256:
            return None
        if time.monotonic() >= self._expires_at:
            await self._refresh()
        key = self._keys.get(key_id)
        if key is None:
            await self._refresh(force=True)
            key = self._keys.get(key_id)
        return key

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and time.monotonic() < self._expires_at:
                return
            owns_client = self._client is None
            client = self._client or httpx.AsyncClient(
                timeout=self.timeout_seconds, follow_redirects=False
            )
            try:
                discovery = await client.get(
                    f"{self.issuer}.well-known/openid-configuration"
                )
                discovery.raise_for_status()
                metadata = discovery.json()
                discovered_issuer = metadata.get("issuer")
                if (
                    not isinstance(discovered_issuer, str)
                    or discovered_issuer.rstrip("/") + "/" != self.issuer
                ):
                    raise ValueError("OIDC discovery issuer mismatch")
                jwks_uri = metadata.get("jwks_uri")
                parsed = urlsplit(jwks_uri) if isinstance(jwks_uri, str) else None
                if (
                    parsed is None
                    or parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.fragment
                ):
                    raise ValueError("OIDC JWKS URI must be an absolute HTTPS URL")
                response = await client.get(jwks_uri)
                response.raise_for_status()
                self._keys = _index_jwks(response.json())
                self._expires_at = time.monotonic() + self.ttl_seconds
            finally:
                if owns_client:
                    await client.aclose()


@dataclass(frozen=True, slots=True)
class _Membership:
    user_id: uuid.UUID
    role: str


class InMemoryMembershipStore:
    def __init__(self) -> None:
        self._memberships: dict[tuple[uuid.UUID, str], _Membership] = {}

    def add(
        self,
        *,
        tenant_id: uuid.UUID,
        subject: str,
        user_id: uuid.UUID,
        role: str,
    ) -> None:
        if role not in ROLE_PERMISSIONS:
            raise ValueError("unsupported membership role")
        self._memberships[(tenant_id, subject)] = _Membership(user_id, role)

    async def find(self, tenant_id: uuid.UUID, subject: str) -> Principal | None:
        membership = self._memberships.get((tenant_id, subject))
        if membership is None:
            return None
        return Principal(
            tenant_id=tenant_id,
            user_id=membership.user_id,
            subject=subject,
            role=membership.role,
            permissions=ROLE_PERMISSIONS[membership.role],
        )


class DatabaseMembershipStore:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self.sessions = sessions

    async def find(self, tenant_id: uuid.UUID, subject: str) -> Principal | None:
        context = TenantContext(tenant_id)
        async with tenant_transaction(self.sessions, context) as database:
            row = (
                await database.execute(
                    select(User.id, Membership.role)
                    .join(Membership, Membership.user_id == User.id)
                    .where(User.external_subject == subject)
                )
            ).one_or_none()
        if row is None or row.role not in ROLE_PERMISSIONS:
            return None
        return Principal(
            tenant_id=tenant_id,
            user_id=row.id,
            subject=subject,
            role=row.role,
            permissions=ROLE_PERMISSIONS[row.role],
        )


class AuthService:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: JwksProvider,
        memberships: MembershipStore,
        allowed_algorithms: tuple[str, ...] = ("RS256", "ES256", "EdDSA"),
        clock_skew_seconds: int = 30,
    ) -> None:
        self.issuer = issuer.rstrip("/") + "/"
        self.audience = audience
        self.jwks = jwks
        self.memberships = memberships
        self.allowed_algorithms = allowed_algorithms
        self.clock_skew_seconds = clock_skew_seconds

    async def verify_token(
        self, token: str, *, expected_nonce: str | None = None
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if algorithm not in self.allowed_algorithms or not isinstance(key_id, str):
                raise InvalidTokenError
            jwk = await self.jwks.get_jwk(key_id)
            if jwk is None or jwk.get("alg") not in {None, algorithm}:
                raise InvalidTokenError
            key = PyJWK.from_dict(dict(jwk), algorithm=algorithm).key
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "tid"]},
            )
            if expected_nonce is not None and claims.get("nonce") != expected_nonce:
                raise InvalidTokenError
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 512:
                raise InvalidTokenError
            uuid.UUID(str(claims.get("tid")))
            return claims
        except (InvalidTokenError, TypeError, ValueError, KeyError) as exc:
            raise ValueError("token validation failed") from exc

    async def authenticate(self, token: str) -> Principal:
        claims = await self.verify_token(token)
        principal = await self.memberships.find(
            uuid.UUID(str(claims["tid"])), str(claims["sub"])
        )
        if principal is None:
            raise ValueError("token validation failed")
        return principal


def _index_jwks(jwks: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_keys = jwks.get("keys")
    if not isinstance(raw_keys, list):
        raise ValueError("JWKS must contain a key list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for key in raw_keys:
        if not isinstance(key, Mapping):
            raise ValueError("JWKS keys must be objects")
        key_id = key.get("kid")
        if not isinstance(key_id, str) or not key_id or len(key_id) > 256:
            raise ValueError("JWKS key ID is invalid")
        if key_id in indexed:
            raise ValueError("JWKS key IDs must be unique")
        if key.get("use") not in {None, "sig"}:
            continue
        indexed[key_id] = key
    if not indexed:
        raise ValueError("JWKS contains no signing keys")
    return indexed
