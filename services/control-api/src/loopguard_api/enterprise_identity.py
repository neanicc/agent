from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .authorization import ROLE_PERMISSIONS


class EnterpriseIdentityRejected(ValueError):
    pass


class TxtResolver(Protocol):
    def records(self, domain: str) -> Sequence[str]: ...


class StaticTxtResolver:
    def __init__(self, values: dict[str, Sequence[str]] | None = None) -> None:
        self.values = {key: tuple(value) for key, value in (values or {}).items()}

    def records(self, domain: str) -> Sequence[str]:
        return self.values.get(domain, ())


class ScimModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ScimIdentity(ScimModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    external_id: str = Field(min_length=1, max_length=256)
    user_name: str = Field(min_length=1, max_length=320)
    display_name: str = Field(min_length=1, max_length=256)
    active: bool = True
    group_ids: tuple[uuid.UUID, ...] = ()
    provisioned_role: str | None = None
    created_at: datetime
    updated_at: datetime


class ScimGroup(ScimModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    external_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    member_ids: tuple[uuid.UUID, ...] = ()
    active: bool = True
    created_at: datetime
    updated_at: datetime


class FederationConfig(ScimModel):
    tenant_id: uuid.UUID
    provider: Literal["oidc", "saml"]
    issuer_or_entity_id: str = Field(min_length=1, max_length=2_048)
    verified_domain: str = Field(min_length=3, max_length=253)
    enabled: bool = True
    group_claim: str = Field(default="groups", min_length=1, max_length=128)

    @field_validator("issuer_or_entity_id")
    @classmethod
    def https_identity_provider(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("enterprise identity provider must use an exact HTTPS identifier")
        return value


@dataclass(frozen=True, slots=True)
class ScimTokenRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    label: str
    salt: bytes
    secret_hash: bytes
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DomainChallenge:
    tenant_id: uuid.UUID
    domain: str
    expected_record: str
    expires_at: datetime
    verified_at: datetime | None = None


class InMemoryEnterpriseIdentityStore:
    """Deterministic adapter. Hosted deployments replace it with a transactional store."""

    durable = False

    def __init__(self) -> None:
        self.tokens: dict[uuid.UUID, ScimTokenRecord] = {}
        self.users: dict[uuid.UUID, ScimIdentity] = {}
        self.groups: dict[uuid.UUID, ScimGroup] = {}
        self.group_mappings: dict[tuple[uuid.UUID, str], str] = {}
        self.domains: dict[tuple[uuid.UUID, str], DomainChallenge] = {}
        self.federation: dict[uuid.UUID, FederationConfig] = {}
        self.idempotency: dict[tuple[uuid.UUID, str], tuple[str, object]] = {}
        self.lock = threading.RLock()


class EnterpriseIdentityService:
    def __init__(
        self,
        *,
        store: InMemoryEnterpriseIdentityStore | None = None,
        resolver: TxtResolver | None = None,
        pepper: bytes = b"loopguard-test-scim-pepper",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if len(pepper) < 16:
            raise ValueError("SCIM token pepper must contain at least 16 bytes")
        self.store = store or InMemoryEnterpriseIdentityStore()
        self.resolver = resolver or StaticTxtResolver()
        self.pepper = pepper
        self.clock = clock

    @property
    def durable(self) -> bool:
        return bool(getattr(self.store, "durable", False))

    def issue_token(
        self,
        tenant_id: uuid.UUID,
        *,
        label: str,
        lifetime: timedelta | None = timedelta(days=90),
    ) -> tuple[ScimTokenRecord, str]:
        label = _bounded(label, 128, "SCIM token label")
        if lifetime is not None and not timedelta(minutes=5) <= lifetime <= timedelta(days=365):
            raise EnterpriseIdentityRejected("SCIM token lifetime must be 5 minutes to 365 days")
        token_id = uuid.uuid4()
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        created = self.clock()
        record = ScimTokenRecord(
            id=token_id,
            tenant_id=tenant_id,
            label=label,
            salt=salt,
            secret_hash=self._hash_secret(secret, salt),
            created_at=created,
            expires_at=created + lifetime if lifetime is not None else None,
        )
        with self.store.lock:
            self.store.tokens[token_id] = record
        return record, f"lgscim_{token_id.hex}.{secret}"

    def rotate_token(
        self,
        tenant_id: uuid.UUID,
        token_id: uuid.UUID,
        *,
        overlap: timedelta = timedelta(minutes=10),
    ) -> tuple[ScimTokenRecord, str]:
        if not timedelta(0) <= overlap <= timedelta(hours=24):
            raise EnterpriseIdentityRejected("SCIM rotation overlap must be at most 24 hours")
        now = self.clock()
        with self.store.lock:
            previous = self._token(tenant_id, token_id)
            self.store.tokens[token_id] = replace(previous, expires_at=now + overlap)
        return self.issue_token(tenant_id, label=f"{previous.label} rotation")

    def revoke_token(self, tenant_id: uuid.UUID, token_id: uuid.UUID) -> None:
        with self.store.lock:
            record = self._token(tenant_id, token_id)
            self.store.tokens[token_id] = replace(record, revoked_at=self.clock())

    def authenticate(self, bearer_token: str) -> uuid.UUID:
        token_id, secret = _parse_token(bearer_token)
        with self.store.lock:
            record = self.store.tokens.get(token_id)
        now = self.clock()
        if (
            record is None
            or record.revoked_at is not None
            or (record.expires_at is not None and now >= record.expires_at)
            or not hmac.compare_digest(
                record.secret_hash,
                self._hash_secret(secret, record.salt),
            )
        ):
            raise EnterpriseIdentityRejected("SCIM bearer token is invalid")
        return record.tenant_id

    def create_user(
        self,
        tenant_id: uuid.UUID,
        *,
        external_id: str,
        user_name: str,
        display_name: str,
        active: bool,
        idempotency_key: str,
    ) -> ScimIdentity:
        payload = f"create-user:{external_id}:{user_name}:{display_name}:{active}"

        def operation() -> ScimIdentity:
            now = self.clock()
            normalized_user = _user_name(user_name)
            external = _bounded(external_id, 256, "SCIM external ID")
            for identity in self.store.users.values():
                if identity.tenant_id != tenant_id:
                    continue
                if identity.external_id == external or identity.user_name.casefold() == normalized_user.casefold():
                    raise EnterpriseIdentityRejected("SCIM user already exists")
            identity = ScimIdentity(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                external_id=external,
                user_name=normalized_user,
                display_name=_bounded(display_name, 256, "SCIM display name"),
                active=active,
                created_at=now,
                updated_at=now,
            )
            self.store.users[identity.id] = identity
            return identity

        return self._idempotent(tenant_id, idempotency_key, payload, operation)

    def update_user(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        active: bool | None,
        display_name: str | None,
        group_ids: Sequence[uuid.UUID] | None,
        idempotency_key: str,
    ) -> ScimIdentity:
        payload = f"update-user:{user_id}:{active}:{display_name}:{group_ids}"

        def operation() -> ScimIdentity:
            current = self.read_user(tenant_id, user_id)
            groups = current.group_ids
            if group_ids is not None:
                unique = tuple(dict.fromkeys(group_ids))
                for group_id in unique:
                    group = self.read_group(tenant_id, group_id)
                    if not group.active:
                        raise EnterpriseIdentityRejected("SCIM group is deprovisioned")
                groups = unique
            updated = current.model_copy(
                update={
                    "active": current.active if active is None else active,
                    "display_name": (
                        current.display_name
                        if display_name is None
                        else _bounded(display_name, 256, "SCIM display name")
                    ),
                    "group_ids": groups,
                    "provisioned_role": self._role_for_groups(tenant_id, groups),
                    "updated_at": self.clock(),
                }
            )
            self.store.users[user_id] = updated
            return updated

        return self._idempotent(tenant_id, idempotency_key, payload, operation)

    def list_users(
        self,
        tenant_id: uuid.UUID,
        *,
        user_name: str | None = None,
    ) -> list[ScimIdentity]:
        normalized = user_name.casefold() if user_name is not None else None
        return sorted(
            (
                value
                for value in self.store.users.values()
                if value.tenant_id == tenant_id
                and (normalized is None or value.user_name.casefold() == normalized)
            ),
            key=lambda value: (value.user_name.casefold(), value.id),
        )

    def read_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> ScimIdentity:
        value = self.store.users.get(user_id)
        if value is None or value.tenant_id != tenant_id:
            raise EnterpriseIdentityRejected("SCIM user does not exist")
        return value

    def create_group(
        self,
        tenant_id: uuid.UUID,
        *,
        external_id: str,
        display_name: str,
        idempotency_key: str,
    ) -> ScimGroup:
        payload = f"create-group:{external_id}:{display_name}"

        def operation() -> ScimGroup:
            external = _bounded(external_id, 256, "SCIM external ID")
            name = _bounded(display_name, 256, "SCIM group display name")
            for group in self.store.groups.values():
                if group.tenant_id == tenant_id and (
                    group.external_id == external or group.display_name.casefold() == name.casefold()
                ):
                    raise EnterpriseIdentityRejected("SCIM group already exists")
            now = self.clock()
            group = ScimGroup(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                external_id=external,
                display_name=name,
                created_at=now,
                updated_at=now,
            )
            self.store.groups[group.id] = group
            return group

        return self._idempotent(tenant_id, idempotency_key, payload, operation)

    def update_group(
        self,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        *,
        member_ids: Sequence[uuid.UUID] | None,
        active: bool | None,
        idempotency_key: str,
    ) -> ScimGroup:
        payload = f"update-group:{group_id}:{member_ids}:{active}"

        def operation() -> ScimGroup:
            current = self.read_group(tenant_id, group_id)
            members = current.member_ids
            if member_ids is not None:
                members = tuple(dict.fromkeys(member_ids))
                for user_id in members:
                    self.read_user(tenant_id, user_id)
            updated = current.model_copy(
                update={
                    "member_ids": members,
                    "active": current.active if active is None else active,
                    "updated_at": self.clock(),
                }
            )
            self.store.groups[group_id] = updated
            for user in tuple(self.store.users.values()):
                if user.tenant_id != tenant_id:
                    continue
                group_ids = set(user.group_ids)
                if user.id in members and updated.active:
                    group_ids.add(group_id)
                else:
                    group_ids.discard(group_id)
                self.store.users[user.id] = user.model_copy(
                    update={
                        "group_ids": tuple(sorted(group_ids, key=str)),
                        "provisioned_role": self._role_for_groups(tenant_id, group_ids),
                        "updated_at": self.clock(),
                    }
                )
            return updated

        return self._idempotent(tenant_id, idempotency_key, payload, operation)

    def list_groups(self, tenant_id: uuid.UUID) -> list[ScimGroup]:
        return sorted(
            (value for value in self.store.groups.values() if value.tenant_id == tenant_id),
            key=lambda value: (value.display_name.casefold(), value.id),
        )

    def read_group(self, tenant_id: uuid.UUID, group_id: uuid.UUID) -> ScimGroup:
        value = self.store.groups.get(group_id)
        if value is None or value.tenant_id != tenant_id:
            raise EnterpriseIdentityRejected("SCIM group does not exist")
        return value

    def map_group(self, tenant_id: uuid.UUID, external_group: str, role: str) -> None:
        if role not in ROLE_PERMISSIONS:
            raise EnterpriseIdentityRejected("group mapping role is unsupported")
        key = _bounded(external_group, 256, "external group").casefold()
        self.store.group_mappings[(tenant_id, key)] = role
        for user in tuple(self.store.users.values()):
            if user.tenant_id == tenant_id:
                self.store.users[user.id] = user.model_copy(
                    update={
                        "provisioned_role": self._role_for_groups(
                            tenant_id, user.group_ids
                        )
                    }
                )

    def create_domain_challenge(
        self,
        tenant_id: uuid.UUID,
        domain: str,
    ) -> DomainChallenge:
        normalized = _domain(domain)
        challenge = DomainChallenge(
            tenant_id=tenant_id,
            domain=normalized,
            expected_record=f"loopguard-verification={secrets.token_urlsafe(32)}",
            expires_at=self.clock() + timedelta(hours=24),
        )
        self.store.domains[(tenant_id, normalized)] = challenge
        return challenge

    def verify_domain(self, tenant_id: uuid.UUID, domain: str) -> DomainChallenge:
        normalized = _domain(domain)
        challenge = self.store.domains.get((tenant_id, normalized))
        if challenge is None or self.clock() >= challenge.expires_at:
            raise EnterpriseIdentityRejected("domain verification challenge is unavailable")
        values = {value.strip().strip('"') for value in self.resolver.records(normalized)}
        if challenge.expected_record not in values:
            raise EnterpriseIdentityRejected("domain verification TXT record was not observed")
        verified = replace(challenge, verified_at=self.clock())
        self.store.domains[(tenant_id, normalized)] = verified
        return verified

    def configure_federation(self, config: FederationConfig) -> None:
        domain = self.store.domains.get((config.tenant_id, config.verified_domain))
        if domain is None or domain.verified_at is None:
            raise EnterpriseIdentityRejected("federation requires a verified domain")
        self.store.federation[config.tenant_id] = config

    def _role_for_groups(
        self,
        tenant_id: uuid.UUID,
        group_ids: Sequence[uuid.UUID] | set[uuid.UUID],
    ) -> str | None:
        precedence = {"viewer": 0, "operator": 1, "admin": 2, "owner": 3}
        roles = []
        for group_id in group_ids:
            group = self.store.groups.get(group_id)
            if group is None or group.tenant_id != tenant_id or not group.active:
                continue
            role = self.store.group_mappings.get((tenant_id, group.display_name.casefold()))
            if role is not None:
                roles.append(role)
        return max(roles, key=precedence.__getitem__) if roles else None

    def _idempotent(
        self,
        tenant_id: uuid.UUID,
        key: str,
        payload: str,
        operation: Callable[[], object],
    ):
        key = _bounded(key, 256, "idempotency key")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.store.lock:
            existing = self.store.idempotency.get((tenant_id, key))
            if existing is not None:
                if not hmac.compare_digest(existing[0], digest):
                    raise EnterpriseIdentityRejected("idempotency key payload conflicts")
                return existing[1]
            result = operation()
            self.store.idempotency[(tenant_id, key)] = (digest, result)
            return result

    def _token(self, tenant_id: uuid.UUID, token_id: uuid.UUID) -> ScimTokenRecord:
        record = self.store.tokens.get(token_id)
        if record is None or record.tenant_id != tenant_id:
            raise EnterpriseIdentityRejected("SCIM token does not exist")
        return record

    def _hash_secret(self, secret: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            secret.encode(),
            salt=salt + self.pepper,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )


def _parse_token(value: str) -> tuple[uuid.UUID, str]:
    prefix, separator, secret = value.partition(".")
    if (
        separator != "."
        or not prefix.startswith("lgscim_")
        or not 32 <= len(secret) <= 128
    ):
        raise EnterpriseIdentityRejected("SCIM bearer token is invalid")
    try:
        token_id = uuid.UUID(hex=prefix.removeprefix("lgscim_"))
    except ValueError as exc:
        raise EnterpriseIdentityRejected("SCIM bearer token is invalid") from exc
    return token_id, secret


def _bounded(value: str, maximum: int, label: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise EnterpriseIdentityRejected(f"{label} is invalid")
    return normalized


def _user_name(value: str) -> str:
    normalized = _bounded(value, 320, "SCIM userName")
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise EnterpriseIdentityRejected("SCIM userName must be an email-like identifier")
    return normalized


def _domain(value: str) -> str:
    normalized = value.strip().rstrip(".").lower()
    if (
        len(normalized) > 253
        or "." not in normalized
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
            for label in normalized.split(".")
        )
    ):
        raise EnterpriseIdentityRejected("enterprise domain is invalid")
    return normalized
