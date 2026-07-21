from __future__ import annotations

import base64
import asyncio
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from loopguard_api.app import create_app
from loopguard_api.auth import AuthService, InMemoryMembershipStore, StaticJwksProvider
from loopguard_api.authorization import Permission
from loopguard_api.settings import Settings


ISSUER = "https://identity.example.test/"
AUDIENCE = "loopguard-control-api"
TENANT_ID = uuid.uuid4()


@pytest.fixture(scope="module")
def signing_material():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def encoded(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "oidc-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": encoded(public_numbers.n),
        "e": encoded(public_numbers.e),
    }
    return private_key, jwk


@pytest.fixture()
def token_factory(signing_material):
    private_key, _ = signing_material

    def make(**updates: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "subject-operator",
            "tid": str(TENANT_ID),
            "iat": now,
            "exp": now + 300,
        }
        claims.update(updates)
        return jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "oidc-key-1"},
        )

    return make


@pytest.fixture()
def auth_service(signing_material) -> AuthService:
    _, jwk = signing_material
    memberships = InMemoryMembershipStore()
    memberships.add(
        tenant_id=TENANT_ID,
        subject="subject-operator",
        user_id=uuid.uuid4(),
        role="operator",
    )
    memberships.add(
        tenant_id=TENANT_ID,
        subject="subject-viewer",
        user_id=uuid.uuid4(),
        role="viewer",
    )
    return AuthService(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks=StaticJwksProvider({"keys": [jwk]}),
        memberships=memberships,
    )


@pytest.fixture()
def client(auth_service: AuthService) -> TestClient:
    return TestClient(
        create_app(
            Settings.for_test(oidc_issuer=ISSUER, oidc_audience=AUDIENCE),
            auth_service=auth_service,
        )
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rejects_wrong_audience(client: TestClient, token_factory):
    response = client.get("/v1/me", headers=bearer(token_factory(aud="wrong")))

    assert response.status_code == 401
    assert response.json()["code"] == "LGAPI-UNAUTHORIZED"


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"iss": "https://attacker.example/"},
        {"exp": 1},
        {"sub": ""},
        {"tid": "not-a-uuid"},
    ],
)
def test_rejects_invalid_trust_claims(client: TestClient, token_factory, claim_overrides):
    response = client.get("/v1/me", headers=bearer(token_factory(**claim_overrides)))

    assert response.status_code == 401


def test_rejects_unknown_signing_key(client: TestClient, token_factory):
    token = token_factory()
    header, payload, signature = token.split(".")
    unknown_header = base64.urlsafe_b64encode(
        b'{"alg":"RS256","kid":"unknown","typ":"JWT"}'
    ).rstrip(b"=").decode()

    response = client.get(
        "/v1/me", headers=bearer(".".join((unknown_header, payload, signature)))
    )

    assert response.status_code == 401


def test_nonce_is_required_when_flow_binds_one(auth_service: AuthService, token_factory):
    with pytest.raises(ValueError, match="token validation failed"):
        asyncio.run(
            auth_service.verify_token(
                token_factory(nonce="other"), expected_nonce="expected"
            )
        )


def test_valid_principal_exposes_identity_not_token(client: TestClient, token_factory):
    response = client.get("/v1/me", headers=bearer(token_factory()))

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": str(TENANT_ID),
        "role": "operator",
        "permissions": [
            Permission.RUN_REPAIR,
            Permission.CONTROL_SESSION,
            Permission.VIEW_SESSION,
        ],
    }


def test_missing_membership_fails_closed(client: TestClient, token_factory):
    response = client.get(
        "/v1/me", headers=bearer(token_factory(sub="unknown-subject"))
    )

    assert response.status_code == 401


def test_viewer_cannot_create_action(client: TestClient, token_factory):
    response = client.post(
        "/v1/actions",
        headers=bearer(token_factory(sub="subject-viewer")),
        json={
            "target": {"kind": "session", "target_id": str(uuid.uuid4())},
            "kind": "interrupt",
        },
    )

    assert response.status_code == 403


def test_operator_permission_is_accepted(client: TestClient, token_factory):
    response = client.post(
        "/v1/actions",
        headers=bearer(token_factory()),
        json={
            "target": {"kind": "session", "target_id": str(uuid.uuid4())},
            "kind": "interrupt",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "authorization_verified"
