from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.authorization import Principal, ROLE_PERMISSIONS
from loopguard_api.enterprise_identity import (
    EnterpriseIdentityRejected,
    EnterpriseIdentityService,
    FederationConfig,
    StaticTxtResolver,
)
from loopguard_api.settings import Settings


NOW = datetime(2026, 7, 30, tzinfo=UTC)


class AdminAuth:
    def __init__(self, tenant_id: uuid.UUID) -> None:
        self.tenant_id = tenant_id

    async def authenticate(self, _token: str) -> Principal:
        return Principal(
            tenant_id=self.tenant_id,
            user_id=uuid.uuid4(),
            subject="enterprise-admin",
            role="admin",
            permissions=ROLE_PERMISSIONS["admin"],
        )


def test_scim_token_is_hashed_rotatable_and_tenant_scoped() -> None:
    tenant = uuid.uuid4()
    other = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    record, token = service.issue_token(tenant, label="Okta production")
    assert token not in repr(service.store.tokens)
    assert service.authenticate(token) == tenant
    with pytest.raises(EnterpriseIdentityRejected):
        service.revoke_token(other, record.id)

    _, replacement = service.rotate_token(tenant, record.id, overlap=timedelta())
    with pytest.raises(EnterpriseIdentityRejected):
        service.authenticate(token)
    assert service.authenticate(replacement) == tenant


def test_scim_idempotency_replays_same_result_and_rejects_conflict() -> None:
    tenant = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    first = service.create_user(
        tenant,
        external_id="okta-123",
        user_name="dev@example.com",
        display_name="Developer",
        active=True,
        idempotency_key="request-1",
    )
    replay = service.create_user(
        tenant,
        external_id="okta-123",
        user_name="dev@example.com",
        display_name="Developer",
        active=True,
        idempotency_key="request-1",
    )
    assert replay == first
    with pytest.raises(EnterpriseIdentityRejected, match="conflicts"):
        service.create_user(
            tenant,
            external_id="okta-456",
            user_name="other@example.com",
            display_name="Other",
            active=True,
            idempotency_key="request-1",
        )


def test_scim_deprovision_is_soft_and_group_role_mapping_is_explicit() -> None:
    tenant = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    user = service.create_user(
        tenant,
        external_id="entra-user-1",
        user_name="owner@example.com",
        display_name="Owner",
        active=True,
        idempotency_key="user-1",
    )
    group = service.create_group(
        tenant,
        external_id="entra-group-1",
        display_name="LoopGuard Operators",
        idempotency_key="group-1",
    )
    service.map_group(tenant, "LoopGuard Operators", "operator")
    service.update_group(
        tenant,
        group.id,
        member_ids=[user.id],
        active=True,
        idempotency_key="group-members-1",
    )
    assert service.read_user(tenant, user.id).provisioned_role == "operator"

    deprovisioned = service.update_user(
        tenant,
        user.id,
        active=False,
        display_name=None,
        group_ids=None,
        idempotency_key="disable-user-1",
    )
    assert deprovisioned.active is False
    assert service.read_user(tenant, user.id).id == user.id


def test_cross_tenant_scim_identifier_fails_closed() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    user = service.create_user(
        tenant_a,
        external_id="user-a",
        user_name="a@example.com",
        display_name="A",
        active=True,
        idempotency_key="a",
    )
    with pytest.raises(EnterpriseIdentityRejected):
        service.read_user(tenant_b, user.id)


def test_federation_requires_dns_verified_domain() -> None:
    tenant = uuid.uuid4()
    resolver = StaticTxtResolver()
    service = EnterpriseIdentityService(resolver=resolver, clock=lambda: NOW)
    challenge = service.create_domain_challenge(tenant, "Example.COM")
    config = FederationConfig(
        tenant_id=tenant,
        provider="oidc",
        issuer_or_entity_id="https://identity.example.com/",
        verified_domain="example.com",
    )
    with pytest.raises(EnterpriseIdentityRejected):
        service.configure_federation(config)
    resolver.values["example.com"] = (challenge.expected_record,)
    service.verify_domain(tenant, "example.com")
    service.configure_federation(config)
    assert service.store.federation[tenant] == config


def test_scim_http_surface_provisions_and_soft_deprovisions() -> None:
    tenant = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    _, token = service.issue_token(tenant, label="HTTP test")
    client = TestClient(
        create_app(
            Settings.for_test(),
            enterprise_identity_service=service,
        )
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "create-user-http",
    }
    created = client.post(
        "/scim/v2/Users",
        headers=headers,
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "http-user",
            "userName": "http@example.com",
            "displayName": "HTTP User",
            "active": True,
        },
    )
    assert created.status_code == 201
    assert created.headers["content-type"].startswith("application/scim+json")
    user_id = created.json()["id"]

    disabled = client.delete(
        f"/scim/v2/Users/{user_id}",
        headers={**headers, "Idempotency-Key": "disable-user-http"},
    )
    assert disabled.status_code == 204
    assert client.get(
        f"/scim/v2/Users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["active"] is False


def test_scim_http_errors_use_scim_media_type_and_hide_cross_tenant_ids() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    _, token_a = service.issue_token(tenant_a, label="A")
    _, token_b = service.issue_token(tenant_b, label="B")
    user = service.create_user(
        tenant_a,
        external_id="a-user",
        user_name="a-user@example.com",
        display_name="A User",
        active=True,
        idempotency_key="a-user",
    )
    client = TestClient(
        create_app(Settings.for_test(), enterprise_identity_service=service)
    )
    response = client.get(
        f"/scim/v2/Users/{user.id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/scim+json")
    assert response.json()["schemas"] == [
        "urn:ietf:params:scim:api:messages:2.0:Error"
    ]

    unauthorized = client.get(
        "/scim/v2/Users",
        headers={"Authorization": f"Bearer {token_a}x"},
    )
    assert unauthorized.status_code == 401


def test_enterprise_admin_can_issue_a_one_time_scim_secret() -> None:
    tenant = uuid.uuid4()
    service = EnterpriseIdentityService(clock=lambda: NOW)
    client = TestClient(
        create_app(
            Settings.for_test(),
            auth_service=AdminAuth(tenant),  # type: ignore[arg-type]
            enterprise_identity_service=service,
        )
    )
    response = client.post(
        "/v1/enterprise/scim-tokens",
        headers={"Authorization": "Bearer admin"},
        json={"label": "Entra production", "lifetime_days": 30},
    )
    assert response.status_code == 201
    token = response.json()["token"]
    assert service.authenticate(token) == tenant
    assert token not in repr(service.store.tokens)
