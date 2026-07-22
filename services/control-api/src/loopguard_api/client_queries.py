from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from .authorization import Permission, Principal


class ManagedRuleWeakened(ValueError):
    pass


OBSERVED_CATEGORIES = ("agent", "judge", "verification", "critic", "repair")
_SEVERITY = {"inform": 0, "warn": 1, "block": 2}


@dataclass(frozen=True, slots=True)
class Usage:
    usage_id: str
    category: str
    amount: Decimal
    currency: str
    provider: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class HostView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    observed_at: datetime
    ttl_seconds: int
    adapter_version: str
    repository_bound: bool


class ControlQueryService:
    def __init__(
        self, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    ) -> None:
        self._clock = clock
        self._profiles: dict[uuid.UUID, dict[str, Any]] = {}
        self._usage: dict[tuple[uuid.UUID, str], Usage] = {}
        self._hosts: dict[uuid.UUID, HostView] = {}
        self._audit: list[dict[str, Any]] = []
        self._resources: dict[str, list[dict[str, Any]]] = {
            "sessions": [],
            "changes": [],
            "verifications": [],
            "repairs": [],
        }
        self.managed_rules = {"wcag-contrast": "block"}

    def read_preferences(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        return self._profiles.get(
            tenant_id,
            {
                "profile_version": 1,
                "source_manifest_hash": "sha256:managed-defaults-v1",
                "rules": [
                    {"id": rule_id, "severity": severity, "managed": True}
                    for rule_id, severity in self.managed_rules.items()
                ],
            },
        )

    def write_preferences(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, rules: list[dict[str, Any]]
    ) -> dict[str, Any]:
        supplied = {
            str(rule.get("id")): str(rule.get("severity")) for rule in rules
        }
        for rule_id, minimum in self.managed_rules.items():
            severity = supplied.get(rule_id)
            if severity is None or severity not in _SEVERITY or _SEVERITY[severity] < _SEVERITY[minimum]:
                raise ManagedRuleWeakened(rule_id)
        current = self.read_preferences(tenant_id)
        profile = {
            "profile_version": int(current["profile_version"]) + 1,
            "source_manifest_hash": "sha256:managed-defaults-v1",
            "rules": rules,
            "updated_by": str(user_id),
        }
        self._profiles[tenant_id] = profile
        return profile

    def record_usage(
        self,
        *,
        tenant_id: uuid.UUID,
        usage_id: str,
        category: str,
        amount: Decimal,
        currency: str,
        provider: str,
        observed_at: datetime,
    ) -> None:
        if category not in OBSERVED_CATEGORIES or amount < 0 or currency != "USD":
            raise ValueError("usage observation is invalid")
        key = (tenant_id, usage_id)
        value = Usage(usage_id, category, amount, currency, provider, observed_at)
        existing = self._usage.get(key)
        if existing is not None and existing != value:
            raise ValueError("usage ID conflicts with an existing observation")
        self._usage[key] = value

    def cost_summary(self, tenant_id: uuid.UUID, window: str) -> dict[str, Any]:
        duration = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}[window]
        start = self._clock() - duration
        totals = {category: Decimal("0.00") for category in OBSERVED_CATEGORIES}
        for (record_tenant, _), usage in self._usage.items():
            if record_tenant == tenant_id and usage.observed_at >= start:
                totals[usage.category] += usage.amount
        return {
            "window": window,
            "currency": "USD",
            "observed": {
                category: f"{amount.quantize(Decimal('0.01')):.2f}"
                for category, amount in totals.items()
            },
            "estimated_avoided_cost": None,
        }

    def add_host(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        observed_at: datetime,
        ttl_seconds: int,
        adapter_version: str,
        repository_bound: bool,
    ) -> uuid.UUID:
        host_id = uuid.uuid4()
        self._hosts[host_id] = HostView(
            host_id,
            tenant_id,
            name,
            observed_at,
            ttl_seconds,
            adapter_version,
            repository_bound,
        )
        return host_id

    def hosts(self, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return [self._host_dict(host) for host in self._hosts.values() if host.tenant_id == tenant_id]

    def host(self, tenant_id: uuid.UUID, host_id: uuid.UUID) -> dict[str, Any] | None:
        host = self._hosts.get(host_id)
        return None if host is None or host.tenant_id != tenant_id else self._host_dict(host)

    def capabilities(self, principal: Principal, host_id: uuid.UUID) -> dict[str, Any]:
        host = self._hosts.get(host_id)
        unavailable = {
            "remote_actions": {"available": False, "reason": "host_unknown"},
            "session_stream": {"available": False, "reason": "host_unknown"},
            "repair": {"available": False, "reason": "feature_flag_disabled"},
        }
        if host is None or host.tenant_id != principal.tenant_id:
            return {"schema_version": 1, "status": "unknown", "features": unavailable, "observed_at": None, "ttl_seconds": 0}
        stale = self._clock() > host.observed_at + timedelta(seconds=host.ttl_seconds)
        if stale:
            unavailable["remote_actions"]["reason"] = "host_health_stale"
            unavailable["session_stream"]["reason"] = "host_health_stale"
            return {"schema_version": 1, "status": "degraded", "features": unavailable, "observed_at": host.observed_at.isoformat(), "ttl_seconds": host.ttl_seconds}
        can_control = Permission.CONTROL_SESSION in principal.permissions
        bound = host.repository_bound
        features = {
            "remote_actions": {"available": can_control and bound, "reason": "ready" if can_control and bound else "role_or_repository_denied"},
            "session_stream": {"available": True, "reason": "ready"},
            "repair": {"available": False, "reason": "feature_flag_disabled"},
        }
        return {"schema_version": 1, "status": "ready", "features": features, "observed_at": host.observed_at.isoformat(), "ttl_seconds": host.ttl_seconds}

    def add_audit(
        self, *, tenant_id: uuid.UUID, action: str, target_kind: str, target_id: str
    ) -> uuid.UUID:
        entry_id = uuid.uuid4()
        self._audit.append({"id": entry_id, "tenant_id": tenant_id, "action": action, "target_kind": target_kind, "target_id": target_id, "created_at": self._clock()})
        return entry_id

    def audit(self, tenant_id: uuid.UUID, *, limit: int = 50) -> list[dict[str, Any]]:
        values = [item for item in self._audit if item["tenant_id"] == tenant_id]
        values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return [{key: str(value) if isinstance(value, uuid.UUID) else value.isoformat() if isinstance(value, datetime) else value for key, value in item.items() if key != "tenant_id"} for item in values[:limit]]

    def add_resource(self, kind: str, *, tenant_id: uuid.UUID, values: dict[str, Any]) -> uuid.UUID:
        resource_id = uuid.uuid4()
        self._resources[kind].append({"id": resource_id, "tenant_id": tenant_id, "created_at": self._clock(), **values})
        return resource_id

    def resources(self, kind: str, tenant_id: uuid.UUID, *, limit: int = 50) -> list[dict[str, Any]]:
        items = [item for item in self._resources[kind] if item["tenant_id"] == tenant_id]
        items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return [self._public(item) for item in items[:limit]]

    def resource(self, kind: str, tenant_id: uuid.UUID, resource_id: uuid.UUID) -> dict[str, Any] | None:
        return next((self._public(item) for item in self._resources[kind] if item["tenant_id"] == tenant_id and item["id"] == resource_id), None)

    @staticmethod
    def encode_cursor(created_at: datetime, resource_id: uuid.UUID) -> str:
        return base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), str(resource_id)], separators=(",", ":")).encode()).decode()

    def _host_dict(self, host: HostView) -> dict[str, Any]:
        return {"id": str(host.id), "name": host.name, "health": {"status": "observed", "observed_at": host.observed_at.isoformat(), "ttl_seconds": host.ttl_seconds}, "adapter_version": host.adapter_version, "repository_bound": host.repository_bound}

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {key: str(value) if isinstance(value, uuid.UUID) else value.isoformat() if isinstance(value, datetime) else value for key, value in item.items() if key != "tenant_id"}
